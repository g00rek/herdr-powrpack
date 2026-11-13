import contextlib
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
    """A temp HOME and XDG_DATA_HOME, so no test touches the real agent folders."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "home").mkdir()
        (root / "data").mkdir()
        with patch.dict(os.environ, {"HOME": str(root / "home"), "XDG_DATA_HOME": str(root / "data")}):
            yield root


class HomeTests(unittest.TestCase):
    def test_links_everything_but_own(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            (main / "skills").mkdir(parents=True)
            (main / "projects").mkdir()
            (main / "CLAUDE.md").write_text("x")
            (main / ".credentials.json").write_text("secret")
            home = root / "data" / "switchr" / "extra"
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
            home = root / "data" / "switchr" / "extra"
            switchr.prepare_home("codex", home)
            (main / "plugins").mkdir()
            switchr.prepare_home("codex", home)
            self.assertTrue((home / "plugins").is_symlink())

    def test_dangling_link_removed(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            (main / "old").mkdir(parents=True)
            home = root / "data" / "switchr" / "extra"
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
            home = root / "data" / "switchr" / "extra"
            home.mkdir(parents=True)
            (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"y": 2}}))
            switchr.prepare_home("claude", home)
            self.assertEqual(json.loads((home / ".claude.json").read_text()),
                             {"mcpServers": {"a": 1}, "oauthAccount": {"y": 2}})

    def test_claude_json_main_invalid_keeps_home(self):
        with sandbox() as root:
            (root / "home" / ".claude").mkdir()
            (root / "home" / ".claude.json").write_text('{"mcpServers": {')
            home = root / "data" / "switchr" / "extra"
            home.mkdir(parents=True)
            (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"y": 2}}))
            switchr.prepare_home("claude", home)
            self.assertEqual(json.loads((home / ".claude.json").read_text()), {"oauthAccount": {"y": 2}})

    def test_claude_json_main_missing_keeps_home(self):
        with sandbox() as root:
            (root / "home" / ".claude").mkdir()
            home = root / "data" / "switchr" / "extra"
            switchr.prepare_home("claude", home)
            self.assertFalse((home / ".claude.json").exists())


class LaunchTests(unittest.TestCase):
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
            home = root / "data" / "switchr" / "extra"
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
