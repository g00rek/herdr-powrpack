import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_switchr_homes import sandbox, switchr


def fake_jwt(claims):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'none'})}.{part(claims)}.sig"



class AccountTests(unittest.TestCase):
    def setUp(self):
        self.herdr = tempfile.TemporaryDirectory()
        patcher = patch.dict(os.environ, {"HERDR_APP_HOME": self.herdr.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.herdr.cleanup)

    def test_add_refuses_bad_and_taken_names(self):
        with sandbox():
            for bad in ("", "Work", "-x", "a b", "x" * 25, "../x", "a/b"):
                with self.assertRaises(switchr.SwitchrError, msg=bad):
                    switchr.add("claude", bad)
            with self.assertRaises(switchr.SwitchrError):
                switchr.add("claude", "main")  # taken by the main account
            with self.assertRaises(switchr.SwitchrError):
                switchr.add("gemini", "ok")
            entry = switchr.add("claude", "work")
            self.assertTrue(entry["pending"])
            self.assertTrue(Path(entry["home"]).is_dir())
            with self.assertRaises(switchr.SwitchrError):
                switchr.add("claude", "work")
            switchr.add("codex", "work")  # same name under another kind is fine

    def test_confirm_refuses_duplicate_identity(self):
        with sandbox():
            switchr.add("codex", "one")
            switchr.add("codex", "two")
            ids = {}
            with patch.object(switchr, "identity", side_effect=lambda kind, home: ids.get(str(home))):
                main_key = "None"
                ids[main_key] = {"email": "m@x", "org": "o"}
                ids[switchr.account("codex", "one")["home"]] = {"email": "m@x", "org": "o"}
                home_one = Path(switchr.account("codex", "one")["home"])
                with self.assertRaises(switchr.SwitchrError):  # same as main
                    switchr.confirm("codex", "one")
                self.assertIsNone(switchr.account("codex", "one"))
                self.assertFalse(home_one.exists())
                # not logged in
                with self.assertRaises(switchr.SwitchrError):
                    switchr.confirm("codex", "two")
                self.assertIsNone(switchr.account("codex", "two"))
            switchr.add("codex", "a")
            switchr.add("codex", "b")
            ha, hb = (switchr.account("codex", n)["home"] for n in "ab")
            ids = {"None": {"email": "m@x", "org": "o"},
                   ha: {"email": "a@x", "org": "o"}, hb: {"email": "a@x", "org": "o"}}
            with patch.object(switchr, "identity", side_effect=lambda kind, home: ids.get(str(home))):
                self.assertFalse(switchr.confirm("codex", "a").get("pending"))
                with self.assertRaises(switchr.SwitchrError):  # same as account a
                    switchr.confirm("codex", "b")
                self.assertIsNone(switchr.account("codex", "b"))
                listed = json.loads((Path(os.environ["HERDR_APP_HOME"]) / "programs.json").read_text())["programs"]
                self.assertIn("switchr-codex-a", listed)
                self.assertNotIn("switchr-codex-b", listed)

    def test_remove_refuses_main(self):
        with sandbox():
            with self.assertRaises(switchr.SwitchrError):
                switchr.remove("claude", "main")

    def test_remove_refuses_while_running(self):
        with sandbox():
            entry = switchr.add("claude", "work")
            with patch.object(switchr, "running_homes", return_value={entry["home"]: [123]}):
                with self.assertRaises(switchr.SwitchrError):
                    switchr.remove("claude", "work")
                with self.assertRaises(switchr.SwitchrError):
                    switchr.rename("claude", "work", "other")
            self.assertTrue(Path(entry["home"]).is_dir())
            with patch.object(switchr, "running_homes", return_value={}):
                switchr.remove("claude", "work")
            self.assertFalse(Path(entry["home"]).exists())
            self.assertIsNone(switchr.account("claude", "work"))

    def test_remove_does_not_delete_what_main_links_to(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            (main / "skills").mkdir(parents=True)
            (main / "skills" / "keep.md").write_text("x")
            entry = switchr.add("claude", "work")
            self.assertTrue((Path(entry["home"]) / "skills").is_symlink())
            with patch.object(switchr, "running_homes", return_value={}):
                switchr.remove("claude", "work")
            self.assertTrue((main / "skills" / "keep.md").exists())

    def test_rename_main_changes_label_only(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            main.mkdir()
            switchr.rename("claude", "main", "personal")
            self.assertEqual(switchr.load_registry()["main"]["claude"], "personal")
            self.assertTrue(switchr.account("claude", "personal")["main"])
            self.assertIsNone(switchr.account("claude", "main"))
            self.assertEqual(os.listdir(main), [])
            self.assertEqual(switchr.load_registry()["main"]["codex"], "main")
            with self.assertRaises(switchr.SwitchrError):
                switchr.rename("claude", "personal", "bad name")

    def test_rename_extra_moves_folder(self):
        with sandbox():
            entry = switchr.add("claude", "old")
            (Path(entry["home"]) / "marker").write_text("1")
            with patch.object(switchr, "running_homes", return_value={}):
                new = switchr.rename("claude", "old", "fresh")
                self.assertEqual(new["name"], "fresh")
                self.assertFalse(Path(entry["home"]).exists())
                self.assertEqual((Path(new["home"]) / "marker").read_text(), "1")
                switchr.add("claude", "other")
                with self.assertRaises(switchr.SwitchrError):
                    switchr.rename("claude", "fresh", "other")
            self.assertIsNone(switchr.account("claude", "old"))

    def test_sync_programs_owns_only_its_keys(self):
        with sandbox():
            path = Path(os.environ["HERDR_APP_HOME"]) / "programs.json"
            path.write_text(json.dumps({"programs": {
                "lazygit": {"name": "lazygit", "run": ["lazygit"]},
                "switchr-claude-old": {"name": "x", "run": ["x"]}}, "other": 1}))
            confirmed = switchr.add("claude", "work")
            switchr.add("codex", "pend")  # pending: not listed
            reg = switchr.load_registry()
            for e in reg["accounts"]:
                if e["name"] == "work":
                    e.pop("pending")
            switchr.save_registry(reg)
            switchr.sync_programs()
            data = json.loads(path.read_text())
            self.assertEqual(data["other"], 1)
            self.assertEqual(set(data["programs"]), {"lazygit", "switchr-claude-work"})
            self.assertEqual(data["programs"]["switchr-claude-work"], {
                "name": "Claude \u00b7 work",
                "run": [str(Path(switchr.__file__).resolve()), "run", "claude", "work"]})
            self.assertEqual(data["programs"]["lazygit"]["run"], ["lazygit"])

    def test_sync_programs_creates_missing_file(self):
        with sandbox():
            switchr.sync_programs()
            self.assertEqual(json.loads((Path(os.environ["HERDR_APP_HOME"]) / "programs.json").read_text()),
                             {"programs": {}})

    def test_guard_refuses_main_inside_main_and_outside_data(self):
        with sandbox() as root:
            (root / "home" / ".claude").mkdir()
            (root / "home" / ".codex").mkdir()
            for kind, bad in (("claude", root / "home" / ".claude"),
                              ("claude", root / "home" / ".claude" / "sub"),
                              ("codex", root / "home" / ".codex"),
                              ("claude", root / "elsewhere"),
                              ("claude", root / "data" / "switchr"),
                              ("claude", root / "data" / "switchr" / "claude"),
                              ("codex", root / "data" / "switchr" / "codex"),
                              ("claude", root / "data" / "switchr" / "codex" / "work"),
                              ("claude", root / "data" / "switchr" / "claude" / "Bad Name"),
                              ("claude", root / "data" / "switchr" / "claude" / "work" / "sub"),
                              ("claude", root / "data" / "switchr" / "gemini" / "work"),
                              ("claude", root / "data")):
                with self.assertRaises(switchr.SwitchrError, msg=str(bad)):
                    switchr.prepare_home(kind, bad)
            self.assertEqual(sorted(os.listdir(root / "home" / ".claude")), [])
            # a registry entry pointing at main must not be removed or renamed
            switchr.save_registry({"main": {"claude": "main", "codex": "main"}, "accounts": [
                {"kind": "claude", "name": "evil", "home": str(root / "home" / ".claude")}]})
            with patch.object(switchr, "running_homes", return_value={}):
                with self.assertRaises(switchr.SwitchrError):
                    switchr.remove("claude", "evil")
                with self.assertRaises(switchr.SwitchrError):
                    switchr.rename("claude", "evil", "fine")
            self.assertTrue((root / "home" / ".claude").is_dir())

    def test_remove_never_wipes_the_kind_folder(self):
        """A registry entry whose home is the folder of every account of a kind."""
        with sandbox() as root:
            switchr.add("claude", "work")
            kind_dir = root / "data" / "switchr" / "claude"
            reg = switchr.load_registry()
            reg["accounts"].append({"kind": "claude", "name": "evil", "home": str(kind_dir)})
            switchr.save_registry(reg)
            with patch.object(switchr, "running_homes", return_value={}):
                with self.assertRaises(switchr.SwitchrError):
                    switchr.remove("claude", "evil")
            self.assertTrue((kind_dir / "work").is_dir())


class IdentityTests(unittest.TestCase):
    def test_codex_identity_from_id_token(self):
        with sandbox() as root:
            home = root / "data" / "switchr" / "codex" / "w"
            home.mkdir(parents=True)
            (home / "auth.json").write_text(json.dumps({"tokens": {
                "id_token": fake_jwt({"email": "a@b.c"}), "account_id": "acct-1"}}))
            self.assertEqual(switchr.identity("codex", home), {"email": "a@b.c", "org": "acct-1"})
            (home / "auth.json").write_text("{}")
            self.assertIsNone(switchr.identity("codex", home))
            (home / "auth.json").unlink()
            self.assertIsNone(switchr.identity("codex", home))

    def test_codex_main_identity_reads_main_folder(self):
        with sandbox() as root:
            (root / "home" / ".codex").mkdir()
            (root / "home" / ".codex" / "auth.json").write_text(json.dumps({"tokens": {
                "id_token": fake_jwt({"email": "m@x"}), "account_id": "o"}}))
            self.assertEqual(switchr.identity("codex", None), {"email": "m@x", "org": "o"})

    def test_claude_identity_uses_home_env_and_logged_in(self):
        with sandbox() as root:
            home = root / "data" / "switchr" / "claude" / "w"
            seen = []

            def fake(h):
                seen.append(h)
                return {"loggedIn": True, "email": "a@b.c", "orgId": "org-1"}
            with patch.object(switchr, "_claude_auth_status", fake):
                self.assertEqual(switchr.identity("claude", home), {"email": "a@b.c", "org": "org-1"})
                self.assertEqual(seen, [home])
            with patch.object(switchr, "_claude_auth_status", lambda h: {"loggedIn": False}):
                self.assertIsNone(switchr.identity("claude", home))
            with patch.object(switchr, "_claude_auth_status", lambda h: None):
                self.assertIsNone(switchr.identity("claude", home))


class RunningTests(unittest.TestCase):
    def test_running_homes_finds_a_process_with_the_variable(self):
        with sandbox() as root:
            home = str(root / "data" / "switchr" / "claude" / "w")
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                    env={**os.environ, "CLAUDE_CONFIG_DIR": home})
            try:
                found = switchr.running_homes()
                self.assertIn(proc.pid, found.get(home, []))
            finally:
                proc.kill()
                proc.wait()
            self.assertNotIn(proc.pid, switchr.running_homes().get(home, []))


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.herdr = tempfile.TemporaryDirectory()
        patcher = patch.dict(os.environ, {"HERDR_APP_HOME": self.herdr.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.herdr.cleanup)

    def test_login_adds_runs_agent_login_in_home_then_confirms(self):
        with sandbox():
            calls = []

            def fake_run(argv, env=None, **kw):
                calls.append((argv, env))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(switchr.subprocess, "run", fake_run), \
                    patch.object(switchr, "identity", return_value={"email": "a@b", "org": "o"}) as ident:
                ident.side_effect = lambda kind, home: None if home is None else {"email": "a@b", "org": "o"}
                message = switchr.login("claude", "work")
            argv, env = calls[0]
            self.assertEqual(argv, ["claude", "auth", "login"])
            self.assertEqual(env["CLAUDE_CONFIG_DIR"], switchr.account("claude", "work")["home"])
            self.assertFalse(switchr.account("claude", "work").get("pending"))
            self.assertIn("work", message)
            self.assertIn("a@b", message)

    def test_login_refuses_an_account_already_logged_in(self):
        with sandbox():
            switchr.add("claude", "bonus")
            reg = switchr.load_registry()
            reg["accounts"][0].pop("pending")
            switchr.save_registry(reg)
            with patch.object(switchr.subprocess, "run") as run:
                with self.assertRaises(switchr.SwitchrError) as raised:
                    switchr.login("claude", "bonus")
            run.assert_not_called()
            self.assertEqual(str(raised.exception),
                             "bonus is already logged in; remove it first to log in again.")
            self.assertIsNotNone(switchr.account("claude", "bonus"))

    def test_login_reports_the_identity_confirm_saw(self):
        """A second identity lookup that comes back empty must not crash login."""
        with sandbox():
            answers = iter([{"email": "a@b", "org": "o"}, None])  # extra, then main

            def ident(kind, home):
                return next(answers, None)
            with patch.object(switchr.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 0)), \
                    patch.object(switchr, "identity", side_effect=ident):
                message = switchr.login("claude", "work")
            self.assertIn("a@b", message)

    def test_login_refuses_main(self):
        with sandbox():
            with self.assertRaises(switchr.SwitchrError):
                switchr.login("codex", "main")


if __name__ == "__main__":
    unittest.main()
