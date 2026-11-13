import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_switchr_accounts import fake_jwt
from tests.test_switchr_homes import sandbox, switchr

CLAUDE_REPLY = {"limits": [
    {"kind": "session", "group": "session", "percent": 12, "resets_at": "2026-11-13T20:00:00Z"},
    {"kind": "weekly", "group": "weekly", "percent": 40, "resets_at": "2026-11-18T00:00:00Z"},
    {"kind": "weekly_scoped", "group": "weekly", "percent": 91, "resets_at": "2026-11-17T00:00:00Z"},
]}
CODEX_REPLY = {"rate_limit": {"primary_window": {"used_percent": 7, "reset_at": 1795000000},
                              "secondary_window": {"used_percent": 33, "reset_at": 1795500000}}}


class Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def write_claude(path, expires_in, refresh="r1"):
    (path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "a1", "refreshToken": refresh,
        "expiresAt": int((time.time() + expires_in) * 1000), "scopes": ["user:inference"]}}))


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.herdr = tempfile.TemporaryDirectory()
        self.cache = tempfile.TemporaryDirectory()
        for p in (patch.dict(os.environ, {"HERDR_APP_HOME": self.herdr.name,
                                          "XDG_CACHE_HOME": self.cache.name}),):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.herdr.cleanup)
        self.addCleanup(self.cache.cleanup)

    def test_claude_usage_parses_limits(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            main.mkdir()
            write_claude(main, 3600)
            with patch("urllib.request.urlopen", return_value=Reply(CLAUDE_REPLY)) as op:
                got = switchr.usage("claude", "main")
            self.assertEqual(got, {"session": 12, "week": 91, "resets": "2026-11-17T00:00:00Z", "state": "ok"})
            req = op.call_args[0][0]
            self.assertEqual(req.get_header("Authorization"), "Bearer a1")
            self.assertEqual(req.get_header("Anthropic-beta"), "oauth-2025-04-20")

    def test_codex_usage_parses_windows(self):
        with sandbox() as root:
            main = root / "home" / ".codex"
            main.mkdir()
            tok = fake_jwt({"exp": time.time() + 3600})
            (main / "auth.json").write_text(json.dumps({"tokens": {
                "access_token": tok, "refresh_token": "r", "account_id": "acc"}}))
            with patch("urllib.request.urlopen", return_value=Reply(CODEX_REPLY)) as op:
                got = switchr.usage("codex", "main")
            self.assertEqual((got["session"], got["week"], got["state"]), (7, 33, "ok"))
            self.assertTrue(got["resets"].startswith("2026-"))
            self.assertEqual(op.call_args[0][0].get_header("Chatgpt-account-id"), "acc")

    def test_no_renew_while_agent_runs(self):
        with sandbox():
            entry = switchr.add("claude", "work")
            write_claude(Path(entry["home"]), 10)
            with patch.object(switchr, "running_homes", return_value={entry["home"]: [5]}), \
                    patch("urllib.request.urlopen") as op:
                got = switchr.usage("claude", "work")
            self.assertEqual(got["state"], "unknown")
            op.assert_not_called()

    def test_agent_appearing_before_lock_blocks_renewal(self):
        with sandbox():
            entry = switchr.add("claude", "work")
            write_claude(Path(entry["home"]), 10)
            answers = iter([{}, {entry["home"]: [9]}])
            with patch.object(switchr, "running_homes", side_effect=lambda: next(answers)), \
                    patch("urllib.request.urlopen") as op:
                got = switchr.usage("claude", "work")
            self.assertEqual(got["state"], "unknown")
            op.assert_not_called()

    def test_main_is_never_renewed(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            main.mkdir()
            write_claude(main, 10)
            before = (main / ".credentials.json").read_bytes()
            with patch("urllib.request.urlopen") as op:
                got = switchr.usage("claude", "main")
            self.assertEqual(got["state"], "unknown")
            op.assert_not_called()
            self.assertEqual((main / ".credentials.json").read_bytes(), before)

    def test_reply_without_expiry_still_stored(self):
        with sandbox():
            entry = switchr.add("claude", "work")
            home = Path(entry["home"])
            write_claude(home, 10)

            def fake(req, timeout=None):
                if req.get_method() == "POST":
                    return Reply({"access_token": "a2", "refresh_token": "r2"})
                return Reply(CLAUDE_REPLY)
            with patch.object(switchr, "running_homes", return_value={}), \
                    patch("urllib.request.urlopen", side_effect=fake):
                switchr.usage("claude", "work")
            oauth = json.loads((home / ".credentials.json").read_text())["claudeAiOauth"]
            self.assertEqual(oauth["refreshToken"], "r2")
            self.assertGreater(oauth["expiresAt"], time.time() * 1000)

    def test_list_survives_failing_account(self):
        with sandbox():
            switchr.add("claude", "work")
            with patch.object(switchr, "usage", side_effect=RuntimeError("boom")):
                rows = switchr.list_accounts()
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["usage"]["state"], "unknown")

    def test_renew_writes_rotated_token(self):
        with sandbox() as root:
            entry = switchr.add("claude", "work")
            home = Path(entry["home"])
            write_claude(home, 10)
            posts = []

            def fake(req, timeout=None):
                if req.get_method() == "POST":
                    posts.append(json.loads(req.data))
                    return Reply({"access_token": "a2", "refresh_token": "r2", "expires_in": 3600})
                return Reply(CLAUDE_REPLY)
            with patch.object(switchr, "running_homes", return_value={}), \
                    patch("urllib.request.urlopen", side_effect=fake):
                got = switchr.usage("claude", "work")
            self.assertEqual(got["state"], "ok")
            self.assertEqual(posts[0]["refresh_token"], "r1")
            oauth = json.loads((home / ".credentials.json").read_text())["claudeAiOauth"]
            self.assertEqual((oauth["accessToken"], oauth["refreshToken"]), ("a2", "r2"))
            self.assertEqual((home / ".credentials.json").stat().st_mode & 0o777, 0o600)

    def test_network_error_returns_cached(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            main.mkdir()
            write_claude(main, 3600)
            with patch("urllib.request.urlopen", return_value=Reply(CLAUDE_REPLY)):
                first = switchr.usage("claude", "main")
            path = switchr._cache_path()
            data = json.loads(path.read_text())
            data["claude/main"]["at"] -= 1000  # stale, so the network is tried
            path.write_text(json.dumps(data))
            with patch("urllib.request.urlopen", side_effect=OSError("down")):
                self.assertEqual(switchr.usage("claude", "main"), first)

    def test_cache_fresh_skips_network(self):
        with sandbox() as root:
            main = root / "home" / ".claude"
            main.mkdir()
            write_claude(main, 3600)
            with patch("urllib.request.urlopen", return_value=Reply(CLAUDE_REPLY)):
                switchr.usage("claude", "main")
            with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
                self.assertEqual(switchr.usage("claude", "main")["state"], "ok")

    def test_logged_out_and_list_order(self):
        with sandbox():
            switchr.add("claude", "work")
            rows = switchr.list_accounts()
            self.assertEqual([(r["kind"], r["name"], r["main"]) for r in rows],
                             [("claude", "main", True), ("claude", "work", False),
                              ("codex", "main", True)])
            self.assertEqual(rows[0]["usage"]["state"], "logged-out")
            self.assertTrue(rows[1]["pending"])

    def test_lock_times_out_when_held(self):
        with sandbox():
            entry = switchr.add("claude", "work")
            with switchr.account_lock("claude", entry):
                with self.assertRaises(switchr.SwitchrError):
                    with switchr.account_lock("claude", entry, wait=0.3):
                        pass


if __name__ == "__main__":
    unittest.main()
