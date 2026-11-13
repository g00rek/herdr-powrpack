import contextlib
import io
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("switchr", os.path.join(os.path.dirname(__file__), "..", "switchr.py"))
switchr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(switchr)


@contextlib.contextmanager
def sandbox():
    """A temp HOME, XDG_DATA_HOME, XDG_CACHE_HOME and HERDR_APP_HOME, so no test
    touches the real agent folders, the usage cache or Herdr's programs file."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in ("home", "data", "cache", "herdr"):
            (root / name).mkdir()
        with patch.dict(os.environ, {"HOME": str(root / "home"), "XDG_DATA_HOME": str(root / "data"),
                                     "XDG_CACHE_HOME": str(root / "cache"),
                                     "HERDR_APP_HOME": str(root / "herdr")}):
            yield root


class HomeTests(unittest.TestCase):
    def test_links_everything_but_own(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            (main / "skills").mkdir(parents=True)
            (main / "projects").mkdir()
            (main / "CLAUDE.md").write_text("x")
            (main / ".credentials.json").write_text("secret")
            home = root / "data" / "switchr" / "claude" / "extra"
            switchr.prepare_home("claude", home)
            for name in ("CLAUDE.md", "skills", "projects"):
                self.assertTrue((home / name).is_symlink(), name)
                self.assertEqual(os.readlink(home / name), str(main / name))
            self.assertFalse(os.path.lexists(home / ".credentials.json"))
            self.assertEqual(home.stat().st_mode & 0o777, 0o700)

    def test_new_main_entry_linked_on_next_prepare(self):
        with sandbox() as root:
            main = root / "home" / ".codex"
            main.mkdir(parents=True)
            home = root / "data" / "switchr" / "codex" / "extra"
            switchr.prepare_home("codex", home)
            (main / "plugins").mkdir()
            switchr.prepare_home("codex", home)
            self.assertTrue((home / "plugins").is_symlink())

    def test_dangling_link_removed(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            (main / "old").mkdir(parents=True)
            home = root / "data" / "switchr" / "claude" / "extra"
            switchr.prepare_home("claude", home)
            self.assertTrue((home / "old").is_symlink())
            shutil.rmtree(main / "old")
            switchr.prepare_home("claude", home)
            self.assertFalse(os.path.lexists(home / "old"))

    def test_claude_json_sync_keeps_own_keys(self):
        with sandbox() as root:
            (root / "home" / ".claude").mkdir()
            (root / "home" / ".claude.json").write_text(json.dumps(
                {"mcpServers": {"a": 1}, "oauthAccount": {"x": 1}}))
            home = root / "data" / "switchr" / "claude" / "extra"
            home.mkdir(parents=True)
            (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"y": 2}}))
            switchr.prepare_home("claude", home)
            self.assertEqual(json.loads((home / ".claude.json").read_text()),
                             {"mcpServers": {"a": 1}, "oauthAccount": {"y": 2}})

    def test_claude_json_main_invalid_keeps_home(self):
        with sandbox() as root:
            (root / "home" / ".claude").mkdir()
            (root / "home" / ".claude.json").write_text('{"mcpServers": {')
            home = root / "data" / "switchr" / "claude" / "extra"
            home.mkdir(parents=True)
            (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"y": 2}}))
            switchr.prepare_home("claude", home)
            self.assertEqual(json.loads((home / ".claude.json").read_text()), {"oauthAccount": {"y": 2}})

    def test_claude_json_main_missing_keeps_home(self):
        with sandbox() as root:
            (root / "home" / ".claude").mkdir()
            home = root / "data" / "switchr" / "claude" / "extra"
            switchr.prepare_home("claude", home)
            self.assertFalse((home / ".claude.json").exists())


    def test_replaced_link_is_set_aside_and_linked_again(self):
        """An agent that saves a shared file by writing a new one over the link
        (Claude's settings.json, Codex's config.toml) must not fork it forever."""
        with sandbox() as root:
            main = root / "home" / ".claude"
            main.mkdir(parents=True)
            (main / "settings.json").write_text('{"shared": true}')
            (main / "agents").mkdir()
            home = root / "data" / "switchr" / "claude" / "extra"
            switchr.prepare_home("claude", home)
            (home / "settings.json").unlink()
            (home / "settings.json").write_text('{"forked": true}')
            (home / "agents").unlink()
            (home / "agents").mkdir()
            (home / "agents" / "a.md").write_text("x")
            (home / ".credentials.json").write_text("own")  # its own, must stay
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                switchr.prepare_home("claude", home)
            for name in ("settings.json", "agents"):
                self.assertTrue((home / name).is_symlink(), name)
                self.assertEqual(os.readlink(home / name), str(main / name))
            self.assertEqual((main / "settings.json").read_text(), '{"shared": true}')
            kept = sorted(p.name for p in (home / ".switchr-forked").iterdir())
            self.assertEqual(len(kept), 2)
            self.assertTrue(kept[0].startswith("agents."), kept)
            self.assertTrue(kept[1].startswith("settings.json."), kept)
            self.assertEqual((home / ".switchr-forked" / kept[1]).read_text(), '{"forked": true}')
            self.assertEqual((home / ".credentials.json").read_text(), "own")
            self.assertEqual(stderr.getvalue().count("\n"), 2)  # one line per file set aside
            self.assertIn("settings.json", stderr.getvalue())
            # nothing more to set aside the next time
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                switchr.prepare_home("claude", home)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(len(list((home / ".switchr-forked").iterdir())), 2)

class LaunchTests(unittest.TestCase):
    def test_run_prints_a_refusal_instead_of_a_traceback(self):
        with sandbox() as root:
            entry = switchr.add("claude", "work")
            reg = switchr.load_registry()
            reg["accounts"][0]["home"] = str(root / "home" / ".claude")  # a broken registry
            switchr.save_registry(reg)
            with patch.object(switchr.os, "execvpe") as execvpe:
                with self.assertRaises(SystemExit) as raised:
                    switchr.run("claude", "work", [])
            self.assertTrue(str(raised.exception.code).startswith("switchr: refusing"), raised.exception.code)
            execvpe.assert_not_called()

    def test_run_prints_an_os_error_instead_of_a_traceback(self):
        with sandbox():
            switchr.add("claude", "work")
            with patch.object(switchr, "prepare_home", side_effect=PermissionError("denied")), \
                    patch.object(switchr.os, "execvpe") as execvpe:
                with self.assertRaises(SystemExit) as raised:
                    switchr.run("claude", "work", [])
            self.assertEqual(raised.exception.code, "switchr: denied")
            execvpe.assert_not_called()

    def test_launch_argv_main_unsets_variable(self):
        with sandbox():
            env, argv = switchr.launch_argv("claude", "main", ["--model", "x"])
            self.assertEqual(env, {"CLAUDE_CONFIG_DIR": None})
            self.assertEqual(argv, ["claude", "--model", "x"])

    def test_launch_argv_extra_sets_home(self):
        with sandbox() as root:
            home = str(root / "data" / "switchr" / "codex" / "work")
            switchr.save_registry({"main": {"claude": "main", "codex": "main"},
                                   "accounts": [{"kind": "codex", "name": "work", "home": home}]})
            env, argv = switchr.launch_argv("codex", "work", ["resume", "abc"])
            self.assertEqual(env, {"CODEX_HOME": home})
            self.assertEqual(argv, ["codex", "resume", "abc"])

    def test_registry_round_trip_and_default(self):
        with sandbox():
            self.assertEqual(switchr.load_registry()["accounts"], [])
            self.assertIsNone(switchr.account("claude", "nope"))
            self.assertTrue(switchr.account("claude", "main")["main"])


@unittest.skipUnless(shutil.which("claude"), "claude CLI not installed")
class RealCliTests(unittest.TestCase):
    def test_real_claude_reads_extra_home(self):
        with sandbox() as root:
            (root / "home" / ".claude").mkdir()
            home = root / "data" / "switchr" / "claude" / "extra"
            switchr.save_registry({"main": {"claude": "main", "codex": "main"},
                                   "accounts": [{"kind": "claude", "name": "t", "home": str(home)}]})
            switchr.prepare_home("claude", home)
            changes, _ = switchr.launch_argv("claude", "t", [])
            env = {**os.environ, **{k: v for k, v in changes.items() if v}}
            out = subprocess.run(["claude", "auth", "status"], env=env, capture_output=True,
                                 text=True, timeout=60)
            status = json.loads(out.stdout)
            self.assertEqual(status["configDirectory"], str(home))
            self.assertIs(status["loggedIn"], False)


if __name__ == "__main__":
    unittest.main()
