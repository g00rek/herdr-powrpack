import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_switchr_homes import sandbox, switchr

SESSION = "d3df825d-4f08-4fb0-8215-b2be4da1fdd5"
SCRIPT = str(Path(switchr.__file__).resolve())


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def terminal(pid, name="claude", argv=None, state="idle", life="live"):
    fg = None if pid is None else {"pid": pid, "name": name, "argv": argv or [name], "cwd": "/w"}
    return {"address": "t1", "state": life, "foreground": fg,
            "status": {"app": "claude-code", "state": state}}


class FakeHc:
    """Scripted `hc`: `gets` is consumed one per `get`; the last one repeats."""

    def __init__(self, gets):
        self.gets = list(gets)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "get":
            return self.gets.pop(0) if len(self.gets) > 1 else self.gets[0]
        if args[0] == "send":
            return {"started": True}
        raise AssertionError(args)

    def sends(self):
        return [c for c in self.calls if c[0] == "send"]


class ResumeArgvTests(unittest.TestCase):
    def test_resume_argv_claude_variants(self):
        cases = {
            ("claude", "--dangerously-skip-permissions", "--resume"):
                ["--dangerously-skip-permissions"],
            ("claude", "-r", "abc", "--model", "x"): ["--model", "x"],
            ("claude", "-c"): [],
            ("/opt/claude/latest/claude", "--resume=abc", "--continue", "--model", "x"):
                ["--model", "x"],
            ("claude", "--resume", "--model", "x"): ["--model", "x"],
        }
        for argv, kept in cases.items():
            self.assertEqual(switchr.resume_argv("claude", list(argv), "S"),
                             [*kept, "--resume", "S"], argv)

    def test_resume_argv_codex(self):
        self.assertEqual(switchr.resume_argv("codex", ["codex", "resume", "old", "--no-daemon"], "S"),
                         ["resume", "S", "--no-daemon"])
        self.assertEqual(switchr.resume_argv("codex", ["codex", "resume", "--last"], "S"),
                         ["resume", "S"])
        self.assertEqual(switchr.resume_argv("codex", ["/x/bin/codex", "--approve-for-me"], "S"),
                         ["resume", "S", "--approve-for-me"])


class ProcTests(unittest.TestCase):
    def setUp(self):
        self.proc = tempfile.TemporaryDirectory()
        self.addCleanup(self.proc.cleanup)
        patcher = patch.object(switchr, "_proc_path",
                               side_effect=lambda pid, leaf: Path(self.proc.name) / str(pid) / leaf)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_proc(self, pid, env, cmdline=("claude",)):
        directory = Path(self.proc.name) / str(pid)
        directory.mkdir()
        (directory / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")
        (directory / "cmdline").write_bytes(b"\0".join(c.encode() for c in cmdline) + b"\0")
        return directory

    def test_account_of_reads_environ(self):
        with sandbox():
            entry = switchr.add("claude", "bonus")
            self.fake_proc(10, {"PATH": "/bin", "CLAUDE_CONFIG_DIR": entry["home"]})
            self.fake_proc(11, {"PATH": "/bin"})
            self.fake_proc(12, {"CLAUDE_CONFIG_DIR": "/somewhere/else"})
            self.fake_proc(13, {"CODEX_HOME": ""}, cmdline=("/x/bin/codex", "--no-daemon"))
            self.assertEqual(switchr.account_of(10), ("claude", "bonus"))
            self.assertEqual(switchr.account_of(11), ("claude", "main"))
            self.assertEqual(switchr.account_of(12), ("claude", "main"))
            self.assertEqual(switchr.account_of(13), ("codex", "main"))
            self.assertIsNone(switchr.account_of(99))

    def test_codex_session_of_skips_subagents(self):
        with sandbox() as root:
            day = root / "home" / ".codex" / "sessions" / "2026" / "11" / "13"
            day.mkdir(parents=True)
            main_id = "01a1cfd3-0fc8-7033-8090-a3a5327b53fd"
            sub_id = "01a1d002-39e8-7970-875c-8b3b833466be"
            files = {}
            for uuid, payload in ((main_id, {"id": main_id, "source": "cli"}),
                                  (sub_id, {"id": sub_id, "parent_thread_id": main_id,
                                            "source": {"subagent": {}}})):
                path = day / f"rollout-2026-11-13T07-26-00-{uuid}.jsonl"
                path.write_text(json.dumps({"type": "session_meta", "payload": payload}) + "\n")
                files[uuid] = path
            os.utime(files[main_id], (1, 1))  # the subagent's file is newer
            fd = self.fake_proc(20, {}, cmdline=("codex",)) / "fd"
            fd.mkdir()
            (fd / "3").symlink_to(root / "home" / ".codex" / "state_5.sqlite")
            (fd / "47").symlink_to(files[main_id])
            (fd / "71").symlink_to(files[sub_id])
            self.assertEqual(switchr.session_of("codex", 20), main_id)
            self.assertIsNone(switchr.session_of("codex", 21))

    def test_claude_session_needs_a_transcript(self):
        with sandbox() as root:
            sessions = root / "home" / ".claude" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "30.json").write_text(json.dumps({"pid": 30, "sessionId": SESSION}))
            self.assertIsNone(switchr.session_of("claude", 30))  # nothing said yet
            project = root / "home" / ".claude" / "projects" / "-w"
            project.mkdir(parents=True)
            (project / f"{SESSION}.jsonl").write_text("{}\n")
            self.assertEqual(switchr.session_of("claude", 30), SESSION)


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        for name, value in (("_clock", self.clock.time), ("_sleep", self.clock.sleep)):
            patcher = patch.object(switchr, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_move(self, hc, accounts, session=SESSION, name="bonus", sessions=None):
        """`accounts`: pid -> account name; `sessions`: pid -> claude sessionId (default SESSION)."""
        sessions = sessions or {}
        with patch.object(switchr, "_hc", hc), \
                patch.object(switchr, "account_of", side_effect=lambda pid, kind=None: (kind or "claude", accounts.get(pid, "main"))), \
                patch.object(switchr, "session_of", return_value=session), \
                patch.object(switchr, "_claude_session_id", side_effect=lambda pid: sessions.get(pid, SESSION)):
            return switchr.move("t1", name)

    def test_move_refuses_working_and_blocked(self):
        with sandbox():
            switchr.add("claude", "bonus")
            for state in ("working", "blocked", None):
                hc = FakeHc([terminal(100, state=state)])
                result = self.run_move(hc, {})
                self.assertFalse(result["ok"], state)
                self.assertEqual(hc.sends(), [], state)
            hc = FakeHc([terminal(100, state="working")])
            self.assertEqual(self.run_move(hc, {})["message"], "Still working — try when it has finished.")

    def test_move_refuses_same_unknown_and_pending_account(self):
        with sandbox():
            switchr.add("claude", "bonus")  # pending: not logged in yet
            hc = FakeHc([terminal(100)])
            self.assertFalse(self.run_move(hc, {})["ok"])
            self.assertEqual(hc.sends(), [])
            self.confirm_bonus()
            for name, accounts in (("bonus", {100: "bonus"}), ("main", {}), ("nope", {})):
                hc = FakeHc([terminal(100)])
                self.assertFalse(self.run_move(hc, accounts, name=name)["ok"], name)
                self.assertEqual(hc.sends(), [], name)

    def test_move_refuses_without_session(self):
        with sandbox():
            self.confirm_bonus()
            hc = FakeHc([terminal(100)])
            result = self.run_move(hc, {}, session=None)
            self.assertFalse(result["ok"])
            self.assertEqual(hc.sends(), [])

    def test_move_gives_up_after_quit_timeout(self):
        with sandbox():
            self.confirm_bonus()
            hc = FakeHc([terminal(100, state="done")])  # never returns to the shell
            result = self.run_move(hc, {})
            self.assertFalse(result["ok"])
            self.assertEqual(result["message"],
                             "The agent did not quit within 20 seconds; nothing was changed.")
            self.assertEqual(hc.sends(), [["send", "t1", "/exit"]])
            self.assertGreaterEqual(self.clock.now, 20)
            self.assertLess(self.clock.now, 21)

    def test_move_happy_path_sends_one_launch(self):
        with sandbox():
            self.confirm_bonus()
            argv = ["/opt/claude/latest/claude", "--dangerously-skip-permissions", "-c"]
            hc = FakeHc([
                terminal(100, argv=argv),  # first look
                terminal(100, argv=argv),  # still quitting
                terminal(50, name="bash", argv=["/usr/bin/bash"]),  # back at the shell
                terminal(200, name="python3", argv=["python3", SCRIPT]),  # switchr starting
                terminal(200, argv=["claude"]),  # claude, still on the old session
                terminal(200, argv=["claude"]),
            ])
            result = self.run_move(hc, {200: "bonus"})
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["message"], "Moved to bonus, same conversation.")
            launch = shlex.join([SCRIPT, "run", "claude", "bonus", "--",
                                 "--dangerously-skip-permissions", "--resume", SESSION])
            self.assertEqual(hc.sends(), [["send", "t1", "/exit"], ["send", "t1", launch]])

    def test_move_waits_for_the_same_conversation(self):
        with sandbox():
            self.confirm_bonus()
            hc = FakeHc([terminal(100), terminal(50, name="bash"), terminal(200)])
            result = self.run_move(hc, {200: "bonus"}, sessions={200: "fresh-one"})
            self.assertFalse(result["ok"])
            self.assertEqual(len(hc.sends()), 2)

    def test_move_codex_uses_quit(self):
        with sandbox():
            switchr.add("codex", "bonus")
            self.confirm_bonus("codex")
            hc = FakeHc([terminal(100, name="codex", argv=["/x/bin/codex", "--no-daemon"]),
                         terminal(50, name="bash"),
                         terminal(200, name="codex")])
            result = self.run_move(hc, {200: "bonus"}, session="01a1d395-9ec6-7f23-84c1-c663a63c59ec")
            self.assertTrue(result["ok"], result)
            self.assertEqual(hc.sends()[0], ["send", "t1", "/quit"])
            self.assertEqual(hc.sends()[1][2], shlex.join(
                [SCRIPT, "run", "codex", "bonus", "--", "resume",
                 "01a1d395-9ec6-7f23-84c1-c663a63c59ec", "--no-daemon"]))

    def confirm_bonus(self, kind="claude"):
        if switchr.account(kind, "bonus") is None:
            switchr.add(kind, "bonus")
        reg = switchr.load_registry()
        for entry in reg["accounts"]:
            entry.pop("pending", None)
        switchr.save_registry(reg)


class WhichTests(unittest.TestCase):
    def test_which_lists_agent_terminals(self):
        listing = {"hidden_finished": 0, "terminals": [
            {"address": "t1", "agent": "claude-code", "state": "live"},
            {"address": "t2", "agent": "bash", "state": "live"},
            {"address": "t3", "agent": "codex", "state": "live"}]}
        gets = {"t1": terminal(100, state="idle"),
                "t3": terminal(300, name="codex", state="working")}

        def hc(args):
            return listing if args[0] == "ls" else gets[args[1]]
        accounts = {100: ("claude", "main"), 300: ("codex", "bonus")}
        with sandbox(), patch.object(switchr, "_hc", hc), \
                patch.object(switchr, "account_of", side_effect=lambda pid, kind=None: accounts[pid]):
            self.assertEqual(switchr.which(), {
                "t1": {"kind": "claude", "account": "main", "main": True, "state": "idle"},
                "t3": {"kind": "codex", "account": "bonus", "main": False, "state": "working"}})


if __name__ == "__main__":
    unittest.main()
