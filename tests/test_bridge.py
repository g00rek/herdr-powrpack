import contextlib
import importlib.util
import fcntl
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("bridge", os.path.join(os.path.dirname(__file__), "..", "bridge.py"))
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


@contextlib.contextmanager
def sandboxed_state():
    """Keep a test off the real profile and state directory.

    Every path the bridge writes to is derived from the home directory at
    import time, so a test that launches has to move all of them. Without this
    the suite creates files under the user's own Browsr state — and passes on
    the author's machine while failing on a clean one, which is how a broken
    test reached the default branch.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "state").mkdir()
        (root / "profile").mkdir()
        (root / "extension").mkdir()
        with patch.object(bridge, "STATE_DIR", root / "state"), \
                patch.object(bridge, "DATA_DIR", root / "data"), \
                patch.object(bridge, "SOCKET_PATH", root / "state" / "control.sock"), \
                patch.object(bridge, "PROFILE_DIR", root / "profile"), \
                patch.object(bridge, "LAUNCH_LOCK", root / "state" / "launch.lock"), \
                patch.object(bridge, "EXTENSION_DIR", root / "extension"), \
                patch.object(bridge, "EXTENSION_STAMP", root / "state" / "extension.stamp"), \
                patch.object(bridge, "PANEL_PANES", root / "state" / "panel-panes.json"), \
                patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data"),
                                        "XDG_CONFIG_HOME": str(root / "config")}):
            yield root


class BridgeTests(unittest.TestCase):
    def test_local_url_filter(self):
        self.assertTrue(bridge.is_local_url("http://localhost:3000/path"))
        self.assertTrue(bridge.is_local_url("https://127.0.0.1:8787"))
        self.assertTrue(bridge.is_local_url("http://[::1]:5173"))
        self.assertFalse(bridge.is_local_url("https://example.com"))
        self.assertFalse(bridge.is_local_url("javascript:alert(1)"))

    def test_focus_event(self):
        event = {"event": "workspace_focused", "data": {"workspace_id": "w7"}}
        context = {"workspace_id": "w7", "workspace_label": "My app"}
        # `clear=True`: run from inside a Herdr pane, HERDR_SOCKET_PATH is set in
        # the real environment and event_command reads it, so the session id
        # asserted below depended on where the suite was run from.
        with patch.dict(os.environ, {
            "HERDR_PLUGIN_EVENT_JSON": json.dumps(event),
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context),
        }, clear=True), tempfile.TemporaryDirectory() as directory:
            # An app home of its own: a switch now carries the colour the
            # panel worked out from the theme, and reading the real one would
            # make this test say whatever his window happens to be wearing.
            with patch.object(bridge, "herdr_session_id", return_value="/run/herdr.sock"), \
                    patch.object(bridge, "app_home", lambda: Path(directory)):
                self.assertEqual(bridge.event_command(), {
                    "type": "workspace",
                    "event": "focused",
                    "session_id": "/run/herdr.sock",
                    "workspace_id": "w7",
                    "label": "My app",
                    "color": "blue",
                    "rest": "grey",
                    "fold": True,
                })


    def test_devtools_port_is_fixed(self):
        """A restart must not move the DevTools port.

        The port is resolved once by the automation wrapper and handed to a
        client as a URL, so a random port per launch silently breaks every agent
        session that was already running.
        """
        launched = {}

        def fake_popen(argv, **kwargs):
            launched["argv"] = argv
            return None

        with sandboxed_state(), \
                patch.object(bridge, "setup", return_value="/usr/bin/chromium"), \
                patch.object(bridge, "bridge_alive", side_effect=[False, False, True]), \
                patch.object(bridge, "browser_alive", return_value=False), \
                patch.object(bridge.subprocess, "Popen", fake_popen), \
                patch.object(bridge, "show_window", lambda: None), \
                patch.object(bridge, "sync_workspaces", lambda: None):
            bridge.ensure_browser(may_launch=True)

        self.assertIn(f"--remote-debugging-port={bridge.DEVTOOLS_PORT}", launched["argv"])
        self.assertNotIn("--remote-debugging-port=0", launched["argv"])
        # Not the port the user's own daily Chrome answers on for the same
        # protocol: the two must never be mistaken for one another.
        self.assertNotEqual(bridge.DEVTOOLS_PORT, 9222)

    def test_switching_workspace_carries_the_whole_list(self):
        """The strip has to be able to come back from a dropped event.

        Workspace events are fire-and-forget: one that happens while the bridge
        is down is never resent, and the group it should have added or removed
        stays wrong for as long as the browser runs. Sending the full list
        alongside the switch — the one event that happens all day and cannot
        race with the workspace it names — makes that self-correcting.
        """
        sent = []
        event = {"event": "workspace_focused"}
        context = {"workspace_id": "w7", "workspace_label": "My app"}

        with patch.dict(os.environ, {
            "HERDR_PLUGIN_EVENT_JSON": json.dumps(event),
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context),
            "HERDR_SOCKET_PATH": "/run/herdr.sock",
        }, clear=True), \
                patch.object(bridge, "bridge_alive", return_value=True), \
                patch.object(bridge, "herdr_workspaces", return_value=[
                    {"workspace_id": "w7", "label": "My app"},
                    {"workspace_id": "w8", "label": "Other"},
                ]), \
                patch.object(bridge, "socket_request", lambda message, **kw: sent.append(message)):
            bridge.hook()

        self.assertEqual([message["type"] for message in sent], ["workspace_set", "workspace"])
        self.assertEqual(
            [item["workspace_id"] for item in sent[0]["workspaces"]], ["w7", "w8"]
        )
        self.assertEqual(sent[0]["session_id"], "/run/herdr.sock")
        self.assertEqual(sent[1]["event"], "focused")

    def test_a_workspace_switch_survives_herdr_not_answering(self):
        sent = []
        event = {"event": "workspace_focused"}
        context = {"workspace_id": "w7", "workspace_label": "My app"}

        with patch.dict(os.environ, {
            "HERDR_PLUGIN_EVENT_JSON": json.dumps(event),
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context),
            "HERDR_SOCKET_PATH": "/run/herdr.sock",
        }, clear=True), \
                patch.object(bridge, "bridge_alive", return_value=True), \
                patch.object(bridge, "herdr_workspaces", return_value=None), \
                patch.object(bridge, "socket_request", lambda message, **kw: sent.append(message)):
            bridge.hook()

        self.assertEqual([message["type"] for message in sent], ["workspace"])

    def test_the_extension_announcing_itself_reconciles_the_strip(self):
        """A service worker that wakes on its own is nobody's launch.

        Chromium stops an idle extension and starts it again on the next event,
        and nothing in the plugin runs when it does. Until the extension itself
        is the trigger, a browser left running for days keeps whatever strip it
        drifted into.
        """
        spec = importlib.util.spec_from_file_location(
            "native_host", os.path.join(os.path.dirname(__file__), "..", "native_host.py")
        )
        native_host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native_host)
        written = []

        # native_host imports bridge by name, which is a different module object
        # from the one this suite loads by path; patch the one it actually holds.
        with patch.object(native_host, "write_native_message", written.append), \
                patch.object(native_host.bridge, "herdr_workspaces", return_value=[
                    {"workspace_id": "w7", "label": "My app"},
                ]), \
                patch.object(native_host.bridge, "herdr_session_id",
                             return_value="/run/herdr.sock"):
            native_host.reconcile_workspaces()

        self.assertEqual([message["type"] for message in written], ["workspace_set"])
        self.assertEqual(written[0]["workspaces"],
                         [{"workspace_id": "w7", "label": "My app", "color": "grey"}])

    def test_status_reports_what_the_panel_has_to_show(self):
        with patch.object(bridge, "browser_alive", return_value=True), \
                patch.object(bridge, "bridge_alive", return_value=True), \
                patch.object(bridge, "extension_is_stale", return_value=False), \
                patch.object(bridge, "herdr_workspaces", return_value=[
                    {"workspace_id": "w7", "label": "My app"},
                    {"workspace_id": "w8"},
                ]), \
                patch.object(bridge, "socket_request", lambda message, **kw: {
                    "ok": True,
                    "result": {"windows": 1, "ungrouped": 1, "groups": [
                        {"title": "My app", "tabs": 2, "adopted": False},
                    ]},
                }):
            status = bridge.browser_status()

        self.assertTrue(status["browser"])
        self.assertTrue(status["bridge"])
        self.assertFalse(status["extension_stale"])
        # A workspace with no label still has to be nameable on screen.
        self.assertEqual(status["workspaces"], ["My app", "w8"])
        self.assertEqual(status["strip"]["windows"], 1)

    def test_status_still_answers_when_nothing_is_running(self):
        """The panel exists for exactly this moment; it must not crash into it."""
        def refuse(message, **kwargs):
            raise RuntimeError("The Chromium bridge did not answer")

        with patch.object(bridge, "browser_alive", return_value=False), \
                patch.object(bridge, "bridge_alive", return_value=False), \
                patch.object(bridge, "extension_is_stale", return_value=True), \
                patch.object(bridge, "herdr_workspaces", return_value=None), \
                patch.object(bridge, "socket_request", refuse):
            status = bridge.browser_status()

        self.assertFalse(status["browser"])
        self.assertIsNone(status["strip"])
        self.assertEqual(status["workspaces"], [])

    def test_only_the_browsr_process_is_ever_signalled(self):
        """Browsr shares its binary with the browser the user lives in.

        The profile directory is the only thing that tells them apart, and the
        helper processes carry it too, so matching on it alone would signal the
        renderers as well as the browser.

        Both argv shapes appear here on purpose. Chromium rewrites its own
        command line into one space-separated blob with no NUL separators, and a
        version of this that only understood the normal shape passed against a
        made-up /proc while finding nothing at all on the running browser.
        """
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            separated = {
                "12": ["chromium", "--type=renderer", "--user-data-dir=/prof/chromium"],
                "13": ["chromium", "--user-data-dir=/somewhere/else"],
                "14": ["python3", "unrelated.py"],
                "15": ["chromium", "--user-data-dir=/prof/chromium-other"],
            }
            for name, argv in separated.items():
                (proc / name).mkdir()
                (proc / name / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
            # The real browser, exactly as Chromium leaves it.
            (proc / "11").mkdir()
            (proc / "11" / "cmdline").write_bytes(
                b"/usr/lib/chromium/chromium --user-data-dir=/prof/chromium --new-window\0"
            )
            (proc / "self").mkdir()
            (proc / "self" / "cmdline").write_bytes(b"not a pid\0")

            with patch.object(bridge, "PROFILE_DIR", Path("/prof/chromium")):
                self.assertEqual(bridge.browser_pids(proc), [11])

    def test_the_key_opens_focuses_then_closes(self):
        """The convention every other Herdr sidebar plugin follows.

        Pressing the key while working in another pane should bring the panel
        under the cursor, not destroy it; only pressing it while already in the
        panel closes it.
        """
        ran = []
        call = lambda *argv: ran.append(argv) or ""

        with sandboxed_state(), \
                patch.object(bridge, "herdr_call", call), \
                patch.object(bridge, "current_tab_id", return_value="w7:t1"):
            # Nothing open yet.
            with patch.object(bridge, "focused_pane_id", return_value="w7:p1"):
                bridge.toggle_panel()
            self.assertIn("open", ran[0])

            bridge.record_panel_pane("w7:t1", "w7:p3")
            ran.clear()

            # Open, but the cursor is elsewhere.
            with patch.object(bridge, "live_pane_ids", return_value={"w7:p1", "w7:p3"}), \
                    patch.object(bridge, "focused_pane_id", return_value="w7:p1"):
                bridge.toggle_panel()
            self.assertEqual(ran, [("plugin", "pane", "focus", "w7:p3")])
            ran.clear()

            # Open, and the cursor is already in it.
            with patch.object(bridge, "live_pane_ids", return_value={"w7:p1", "w7:p3"}), \
                    patch.object(bridge, "focused_pane_id", return_value="w7:p3"):
                bridge.toggle_panel()
            self.assertEqual(ran, [("plugin", "pane", "close", "w7:p3")])

    def test_a_panel_that_died_without_tidying_up_reopens(self):
        """A crash leaves the recorded pane id behind; the key must still work."""
        ran = []

        with sandboxed_state(), \
                patch.object(bridge, "herdr_call", lambda *argv: ran.append(argv) or ""), \
                patch.object(bridge, "current_tab_id", return_value="w7:t1"), \
                patch.object(bridge, "focused_pane_id", return_value="w7:p1"), \
                patch.object(bridge, "live_pane_ids", return_value={"w7:p1"}):
            bridge.record_panel_pane("w7:t1", "w7:p3")
            bridge.toggle_panel()

        self.assertIn("open", ran[0])

    def test_the_panel_in_another_tab_is_left_alone(self):
        ran = []

        with sandboxed_state(), \
                patch.object(bridge, "herdr_call", lambda *argv: ran.append(argv) or ""), \
                patch.object(bridge, "live_pane_ids", return_value={"w7:p3", "w8:p2"}), \
                patch.object(bridge, "focused_pane_id", return_value="w8:p2"):
            bridge.record_panel_pane("w7:t1", "w7:p3")
            bridge.record_panel_pane("w8:t1", "w8:p2")
            with patch.object(bridge, "current_tab_id", return_value="w8:t1"):
                bridge.toggle_panel()

        self.assertEqual(ran, [("plugin", "pane", "close", "w8:p2")])

    def test_the_browser_follows_the_focused_workspace_however_it_was_switched(self):
        """Herdr does not call a plugin when the user switches with the mouse.

        Verified against this machine's Herdr 0.9.1: a switch made through the
        API is dispatched to plugins within the same second, while six switches
        made in the UI left no plugin invocation at all, though the server
        recorded every one of them. Waiting to be told is therefore not a
        mechanism; the host asks instead, and speaks only when the answer
        changes.
        """
        spec = importlib.util.spec_from_file_location(
            "native_host", os.path.join(os.path.dirname(__file__), "..", "native_host.py")
        )
        native_host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native_host)
        written = []

        def listing(focused):
            return [
                {"workspace_id": "w7", "label": "app", "focused": focused == "w7"},
                {"workspace_id": "w8", "label": "docs", "focused": focused == "w8"},
            ]

        with patch.object(native_host, "write_native_message", written.append), \
                patch.object(native_host.bridge, "herdr_session_id", return_value="/run/h.sock"):
            last = native_host.focus_step(None, listing("w7"))
            self.assertEqual(last, "w7")
            # Nothing changed, so nothing is said.
            last = native_host.focus_step(last, listing("w7"))
            self.assertEqual(len(written), 1)

            last = native_host.focus_step(last, listing("w8"))
            self.assertEqual(last, "w8")

            # Nothing focused must not be read as a switch.
            last = native_host.focus_step(last, listing(None))
            self.assertEqual(last, "w8")

        self.assertEqual([m["event"] for m in written], ["focused", "focused"])
        self.assertEqual([m["workspace_id"] for m in written], ["w7", "w8"])
        self.assertEqual(written[1]["label"], "docs")

    def test_an_exiting_bridge_does_not_delete_the_live_one(self):
        """Two hosts overlap whenever the extension reconnects.

        The newer one rebinds the same path; the older one then exits and, if it
        tidies up, deletes the socket the live host is listening on. The bridge
        then looks dead to every caller, which is how an extra browser window
        got opened in the first place. Told apart by inode this is still wrong —
        the number is reused at once, which CI proved and this machine hid.
        """
        spec = importlib.util.spec_from_file_location(
            "native_host", os.path.join(os.path.dirname(__file__), "..", "native_host.py")
        )
        native_host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native_host)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sock"
            with patch.object(native_host, "SOCKET_PATH", path):
                native_host.stopping.clear()
                older = threading.Thread(target=native_host.socket_server, daemon=True)
                older.start()
                for _ in range(100):
                    if path.exists():
                        break
                    time.sleep(0.02)
                self.assertTrue(path.exists(), "the host never bound its socket")

                # A newer host takes the path over, exactly as Chromium's next
                # one does.
                path.unlink()
                newer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                newer.bind(str(path))
                newer.listen(4)

                native_host.stopping.set()
                older.join(timeout=5)

                self.assertTrue(path.exists(), "the live host's socket was deleted")
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(2)
                client.connect(str(path))  # raises if the live socket is gone
                client.close()
                newer.close()
            native_host.stopping.clear()

    def test_a_checkout_is_not_mistaken_for_an_edited_extension(self):
        """Touching a file is not changing it.

        The fingerprint used size and modification time, so every branch switch
        announced that the browser was running stale code and asked for a
        restart it did not need.
        """
        with sandboxed_state() as root:
            extension = root / "extension"
            (extension / "service-worker.js").write_text("same content")
            before = bridge.extension_fingerprint()

            os.utime(extension / "service-worker.js", (1_000_000, 1_000_000))
            self.assertEqual(bridge.extension_fingerprint(), before, "a touch read as an edit")

            (extension / "service-worker.js").write_text("different content")
            self.assertNotEqual(bridge.extension_fingerprint(), before, "an edit went unnoticed")

    def test_the_watcher_notices_a_workspace_disappearing_too(self):
        """The strip has to keep matching, not just point at the right group."""
        spec = importlib.util.spec_from_file_location(
            "native_host", os.path.join(os.path.dirname(__file__), "..", "native_host.py")
        )
        native_host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native_host)
        written = []

        both = [
            {"workspace_id": "w7", "label": "app", "focused": True},
            {"workspace_id": "w8", "label": "docs"},
        ]
        with patch.object(native_host, "write_native_message", written.append), \
                patch.object(native_host.bridge, "herdr_session_id", return_value="/run/h.sock"):
            seen = native_host.workspace_step(None, both)
            self.assertEqual([m["type"] for m in written], ["workspace_set"])

            # Same list again: nothing to say.
            seen = native_host.workspace_step(seen, both)
            self.assertEqual(len(written), 1)

            # One closed.
            seen = native_host.workspace_step(seen, both[:1])
            self.assertEqual(len(written), 2)
            self.assertEqual([w["workspace_id"] for w in written[1]["workspaces"]], ["w7"])

            # A rename counts as a change too.
            native_host.workspace_step(
                seen, [{"workspace_id": "w7", "label": "renamed", "focused": True}]
            )
            self.assertEqual(written[2]["workspaces"][0]["label"], "renamed")

    def test_cleanup_removes_what_we_put_outside_our_own_directory(self):
        """Herdr has no uninstall hook, so this has to be asked for.

        Uninstalling unregisters the plugin and deletes its checkout; the files
        this one writes elsewhere — the two manifests that let Chromium talk to
        Herdr, and the state directory — would otherwise stay behind for good.
        """
        with sandboxed_state() as root:
            hosts = [
                root / "profile" / "NativeMessagingHosts" / "dev.herdr.browser.json",
                root / "chromium-config" / "NativeMessagingHosts" / "dev.herdr.browser.json",
            ]
            for path in hosts:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            (root / "state" / "control.sock").write_text("")
            (root / "profile" / "Default").mkdir(parents=True, exist_ok=True)
            (root / "profile" / "Default" / "History").write_text("x" * 2048)

            with patch.object(bridge, "NATIVE_HOST_MANIFESTS", hosts):
                removed, profile, size = bridge.cleanup()

            # Inside the temporary directory on purpose: asserting that a file
            # is gone after the directory holding it has been swept away proves
            # nothing at all.
            self.assertTrue(all(not path.exists() for path in hosts), "a manifest survived")
            self.assertFalse((root / "state").exists(), "the state directory survived")
            self.assertEqual(sorted(removed), sorted(str(p) for p in hosts + [root / "state"]))
            # The profile holds the user's own browsing; it is reported, never deleted.
            self.assertTrue(profile.exists(), "the browsing profile was deleted")
            self.assertGreater(size, 2000)

    def test_the_caller_decides_how_long_the_extension_may_take(self):
        """The host applied a fixed eight seconds of its own, so the twenty
        the full workspace set asks for were reported as a timeout while the
        extension was still working — and then it finished the job anyway."""
        spec = importlib.util.spec_from_file_location(
            "native_host", os.path.join(os.path.dirname(__file__), "..", "native_host.py")
        )
        native_host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native_host)

        self.assertEqual(native_host.client_timeout({"timeout": 20}), 20)
        self.assertEqual(native_host.client_timeout({}), 8)
        for nonsense in ({"timeout": 0}, {"timeout": -1}, {"timeout": "soon"},
                         {"timeout": None}, {"timeout": 10 ** 9}):
            self.assertEqual(native_host.client_timeout(nonsense), 8, nonsense)

    def test_a_request_carries_the_patience_its_caller_asked_for(self):
        sent = []

        class FakeSocket:
            def settimeout(self, value):
                sent.append(("settimeout", value))

            def connect(self, _):
                pass

            def sendall(self, raw):
                sent.append(("sent", json.loads(raw)))

            def recv(self, _):
                return b'{"ok": true}\n'

            def close(self):
                pass

        with patch.object(bridge.socket, "socket", lambda *a: FakeSocket()):
            bridge.socket_request({"type": "workspace_set"}, timeout=20)

        self.assertEqual(sent[0], ("settimeout", 22))
        self.assertEqual(sent[1][1]["timeout"], 20)

    def test_setup_does_not_register_the_host_with_the_users_own_browser(self):
        """Older versions wrote a second manifest into ~/.config/chromium.

        Chromium looks for native hosts under the profile it was started with,
        and Browsr always starts with its own, so that copy was never read by
        Browsr — it only offered the host to the browser the user lives in,
        the one the project promises not to touch.
        """
        with sandboxed_state() as root:
            # Asserted on the list itself, not on what turns up under the
            # sandbox: a stray write would land in the real home directory,
            # outside the sandbox, where looking for it would find nothing
            # and the test would pass for being blind.
            self.assertEqual(
                bridge.native_host_manifests(),
                [bridge.PROFILE_DIR / "NativeMessagingHosts" / "dev.herdr.browser.json"],
            )

            with patch.object(bridge, "find_chromium", lambda: "/usr/bin/chromium"), \
                    patch.object(bridge, "NATIVE_HOST_MANIFESTS", None):
                bridge.setup()

            self.assertEqual(
                [p.relative_to(root) for p in root.rglob("dev.herdr.browser.json")],
                [Path("profile/NativeMessagingHosts/dev.herdr.browser.json")],
            )

    def test_cleanup_still_removes_the_manifest_older_versions_left_behind(self):
        with sandboxed_state() as root:
            legacy = root / "config" / "NativeMessagingHosts" / "dev.herdr.browser.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}")

            with patch.object(bridge, "NATIVE_HOST_MANIFESTS", None), \
                    patch.object(bridge, "LEGACY_HOST_MANIFEST", legacy):
                removed, _, _ = bridge.cleanup()

            self.assertFalse(legacy.exists(), "the old manifest was left behind")
            self.assertIn(str(legacy), removed)

    def test_cleanup_is_quiet_when_there_is_nothing_to_remove(self):
        with sandboxed_state() as root:
            missing = [root / "nowhere" / "dev.herdr.browser.json"]
            with patch.object(bridge, "NATIVE_HOST_MANIFESTS", missing):
                removed, profile, size = bridge.cleanup()
            self.assertEqual(removed, [str(root / "state")])

    def test_a_silent_failure_is_announced_once(self):
        """Browser up, extension not answering: tabs stop following, quietly.

        That is the failure that went unnoticed for a week. It is only worth
        announcing when the browser is actually running — a closed browser is
        not a fault — and only after it has lasted, because the extension is a
        service worker Chromium stops and starts at will.
        """
        shown = []
        state = None

        with patch.object(bridge, "browser_alive", return_value=True), \
                patch.object(bridge, "bridge_alive", return_value=False), \
                patch.object(bridge, "herdr_call", lambda *argv: shown.append(argv) or ""):
            state = bridge.health_step(None, 1000.0)
            self.assertEqual(shown, [], "announced before the grace period")

            state = bridge.health_step(state, 1000.0 + bridge.HEALTH_GRACE_SECONDS - 1)
            self.assertEqual(shown, [], "announced one second early")

            state = bridge.health_step(state, 1000.0 + bridge.HEALTH_GRACE_SECONDS)
            self.assertEqual(len(shown), 1, "never announced")
            self.assertEqual(shown[0][:2], ("notification", "show"))

            # Still broken a minute later: say it once, not every minute.
            state = bridge.health_step(state, 2000.0)
            self.assertEqual(len(shown), 1, "repeated itself")

    def test_a_closed_browser_is_not_a_fault(self):
        shown = []
        with patch.object(bridge, "browser_alive", return_value=False), \
                patch.object(bridge, "bridge_alive", return_value=False), \
                patch.object(bridge, "herdr_call", lambda *argv: shown.append(argv) or ""):
            state = bridge.health_step(None, 1000.0)
            state = bridge.health_step(state, 1000.0 + bridge.HEALTH_GRACE_SECONDS * 5)
        self.assertEqual(shown, [])

    def test_recovering_arms_the_warning_again(self):
        shown = []
        healthy = {"browser": True, "bridge": True}

        def alive(which):
            return lambda: healthy[which]

        with patch.object(bridge, "browser_alive", alive("browser")), \
                patch.object(bridge, "bridge_alive", alive("bridge")), \
                patch.object(bridge, "herdr_call", lambda *argv: shown.append(argv) or ""):
            healthy["bridge"] = False
            state = bridge.health_step(None, 0.0)
            state = bridge.health_step(state, bridge.HEALTH_GRACE_SECONDS)
            self.assertEqual(len(shown), 1)

            healthy["bridge"] = True
            state = bridge.health_step(state, bridge.HEALTH_GRACE_SECONDS + 1)

            healthy["bridge"] = False
            state = bridge.health_step(state, bridge.HEALTH_GRACE_SECONDS + 2)
            state = bridge.health_step(state, bridge.HEALTH_GRACE_SECONDS * 3)

        self.assertEqual(len(shown), 2, "a second outage went unannounced")

    def test_only_the_launch_action_may_start_the_browser(self):
        """Browsr is a window on the user's screen; it appears when asked for.

        Every other path — an agent opening a page, a question about the tabs,
        and above all the browser tooling that starts with every agent session
        — used to launch it, so the browser appeared on its own whenever a
        session happened to start.
        """
        for command, arguments in (
            (bridge.open_url, ("http://localhost:3000/",)),
            (bridge.open_tab, ("about:blank#x",)),
            (bridge.workspace_tabs, ()),
            (bridge.devtools_endpoint, ()),
            (bridge.open_link, ()),
        ):
            launched = []
            with sandboxed_state(), \
                    patch.dict(os.environ, {
                        "HERDR_WORKSPACE_ID": "w7",
                        "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({
                            "workspace_id": "w7", "clicked_url": "http://localhost:3000/",
                        }),
                    }, clear=True), \
                    patch.object(bridge, "setup", return_value="/usr/bin/chromium"), \
                    patch.object(bridge, "bridge_alive", return_value=False), \
                    patch.object(bridge, "browser_alive", return_value=False), \
                    patch.object(bridge.subprocess, "Popen", lambda *a, **k: launched.append(a)):
                with self.assertRaises(RuntimeError, msg=command.__name__):
                    command(*arguments)
            self.assertEqual(launched, [], f"{command.__name__} started the browser")

    def launch_under(self, host):
        """Run the launch action and report what it started, as argv lists."""
        started = []
        with sandboxed_state(), \
                patch.object(bridge, "HOST_OVERRIDE", host), \
                patch.object(bridge, "setup", return_value="/usr/bin/chromium"), \
                patch.object(bridge, "bridge_alive", side_effect=[False, False, True]), \
                patch.object(bridge, "browser_alive", return_value=False), \
                patch.object(bridge.subprocess, "Popen",
                             lambda argv, *a, **k: started.append(list(argv))), \
                patch.object(bridge, "show_window", lambda: True), \
                patch.object(bridge, "sync_workspaces", lambda: None):
            bridge.launch()
        return started

    def test_the_launch_action_still_starts_it(self):
        started = self.launch_under("tui")
        self.assertEqual(len(started), 1, "the one action that may start it did not")
        self.assertIn("/usr/bin/chromium", started[0][0])

    def test_under_the_app_the_launch_action_also_starts_the_watcher(self):
        """The app has no startup hook and no plugins, so nothing else would,
        and without it the browser stops following the space in front."""
        started = self.launch_under("app")
        self.assertEqual(len(started), 2, [argv[:2] for argv in started])
        self.assertIn("/usr/bin/chromium", started[0][0])
        self.assertEqual(started[1][-2:], ["--app", "watch"])

    def test_under_the_terminal_herdr_no_second_watcher_is_started(self):
        """There the plugin's own startup line runs it, once per machine."""
        self.assertEqual(len(self.launch_under("tui")), 1)

    def test_a_running_browser_is_never_launched_a_second_time(self):
        """Chromium refuses to start twice; the second start opens a window.

        Every extra window is what multiplied the tab strip: the extension then
        has to guess which window is the real one, and the groups sitting in the
        other one look missing and get remade. So when Chromium answers but the
        bridge does not, the only correct move is to wait for the extension.
        """
        launched = []

        with sandboxed_state(), \
                patch.object(bridge, "setup", return_value="/usr/bin/chromium"), \
                patch.object(bridge, "bridge_alive", return_value=False), \
                patch.object(bridge, "browser_alive", return_value=True), \
                patch.object(bridge, "BRIDGE_WAIT_SECONDS", 0.05), \
                patch.object(bridge.subprocess, "Popen", lambda *a, **k: launched.append(a)), \
                patch.object(bridge, "sync_workspaces", lambda: None):
            with self.assertRaises(RuntimeError):
                bridge.ensure_browser(may_launch=True)

        self.assertEqual(launched, [])

    def test_devtools_endpoint_reports_the_port_that_answers(self):
        """The stale port file must never reach the automation wrapper.

        Verified against the installed Chromium 152: `DevToolsActivePort` is
        written only when the port is left to Chromium (`--remote-debugging-port=0`).
        With a fixed port the file is never refreshed, so whatever it holds is a
        leftover from an older run, and handing that number to the client fails
        as "could not connect" long after the port died.
        """
        printed = []

        with patch.object(bridge, "ensure_browser", lambda **kwargs: None), \
                patch.object(bridge, "devtools_alive", lambda port: port == bridge.DEVTOOLS_PORT), \
                patch("builtins.print", lambda *args, **kwargs: printed.append(args[0])):
            bridge.devtools_endpoint()

        self.assertEqual(printed, [f"http://127.0.0.1:{bridge.DEVTOOLS_PORT}"])

    def test_devtools_endpoint_refuses_a_port_nothing_answers_on(self):
        printed = []

        with patch.object(bridge, "ensure_browser", lambda **kwargs: None), \
                patch.object(bridge, "devtools_alive", lambda port: False), \
                patch("builtins.print", lambda *args, **kwargs: printed.append(args[0])):
            with self.assertRaises(RuntimeError):
                bridge.devtools_endpoint()

        self.assertEqual(printed, [])

    def test_setup_survives_several_callers_at_once(self):
        """After a reboot every caller runs setup() at the same moment."""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "manifest.json"
            writer = (
                "import importlib.util, sys\n"
                f"spec = importlib.util.spec_from_file_location('bridge', {str(Path(bridge.__file__).resolve())!r})\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(module)\n"
                "from pathlib import Path\n"
                "for _ in range(40):\n"
                f"    module.atomic_json(Path({str(target)!r}), {{'ok': True}})\n"
            )
            writers = [subprocess.Popen([sys.executable, "-c", writer]) for _ in range(4)]
            codes = [writer_process.wait() for writer_process in writers]

            self.assertEqual(codes, [0, 0, 0, 0])
            self.assertEqual(json.loads(target.read_text()), {"ok": True})

    def test_a_fresh_install_names_its_directories_after_the_project(self):
        """The first versions used a working name from before the project had
        one, and that name is what the uninstall message shows people."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(bridge.Path, "home", staticmethod(lambda: home)):
                self.assertEqual(bridge._home_dir("state"), home / ".local/state/browsr")
                self.assertEqual(bridge._home_dir("share"), home / ".local/share/browsr")

    def test_an_install_that_already_has_the_old_directory_keeps_it(self):
        """It holds the browser profile — history and logins. Moving that to
        tidy up a name is a trade nobody asked for."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".local/share/herdr-dev-browser").mkdir(parents=True)
            with patch.object(bridge.Path, "home", staticmethod(lambda: home)):
                self.assertEqual(
                    bridge._home_dir("share"), home / ".local/share/herdr-dev-browser"
                )

    def test_the_native_host_looks_for_the_socket_where_the_bridge_puts_it(self):
        """Both used to spell the path out separately, which is a way for them
        to stop agreeing after one of them is edited."""
        spec = importlib.util.spec_from_file_location(
            "native_host", os.path.join(os.path.dirname(__file__), "..", "native_host.py")
        )
        native_host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native_host)

        self.assertEqual(native_host.SOCKET_PATH, bridge.SOCKET_PATH)
        self.assertEqual(native_host.STATE_DIR, bridge.STATE_DIR)

    def test_the_app_is_told_apart_from_the_terminal_herdr(self):
        """They are two programs with their own spaces and their own ids.

        A pane of the terminal one carries HERDR_SOCKET_PATH; a terminal the
        app started carries HC_TERMINAL_ID, because herdr-core puts it there.
        """
        with patch.object(bridge, "HOST_OVERRIDE", None):
            with patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/run/herdr.sock"}, clear=True):
                self.assertEqual(bridge.herdr_host(), "tui")
            with patch.dict(os.environ, {"HC_TERMINAL_ID": "t8"}, clear=True):
                self.assertEqual(bridge.herdr_host(), "app")

        with patch.object(bridge, "HOST_OVERRIDE", "app"), \
                patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/run/herdr.sock"}):
            self.assertEqual(bridge.herdr_host(), "app", "the caller's word did not win")

    def test_an_installed_binary_is_not_a_running_terminal_herdr(self):
        """The terminal Herdr was retired on 2026-11-04 but its binary stays
        on disk. Outside a pane Browsr has to ask what is answering, not what
        is installed, or every call made by hand goes to a dead Herdr."""
        with patch.object(bridge, "HOST_OVERRIDE", None), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(bridge.shutil, "which", lambda name: "/usr/bin/herdr"), \
                patch.object(bridge, "tui_running", lambda: False), \
                patch.object(bridge, "app_running", lambda: True):
            self.assertEqual(bridge.herdr_host(), "app")
        with patch.object(bridge, "HOST_OVERRIDE", None), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(bridge, "tui_running", lambda: True):
            self.assertEqual(bridge.herdr_host(), "tui")

    def test_the_health_watch_is_picked_up_when_its_holder_goes(self):
        """One Herdr shuts down for good and the other's watcher is already
        running. Nothing would report on the browser again until something
        restarted it, because the lock was tried once at startup."""
        with tempfile.TemporaryDirectory() as directory:
            health = Path(directory) / "health.lock"
            focus = Path(directory) / "focus-app.lock"
            other = health.open("w")
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(bridge, "HOST_OVERRIDE", "app"):
                following, checking, held = bridge.take_watch_locks(health, focus)
            self.assertEqual((following, checking), (True, False))

            other.close()  # that Herdr is gone
            later = bridge.claim_lock(health)
            self.assertIsNotNone(later, "the health watch was never picked up")
            later.close()
            for handle in held:
                handle.close()

    def test_the_two_herdrs_never_share_a_namespace(self):
        """One namespace for both would hand each the other's tab groups."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(bridge, "HOST_OVERRIDE", "app"):
                app = bridge.herdr_session_id()
            with patch.object(bridge, "HOST_OVERRIDE", "tui"), \
                    patch.object(bridge, "_HERDR_SOCKET_CACHE", "/run/herdr.sock"):
                tui = bridge.herdr_session_id()
        self.assertEqual(app, str(bridge.app_home()))
        self.assertNotEqual(app, tui)

    def test_the_app_space_list_is_translated_once(self):
        answer = json.dumps({"spaces": [
            {"id": "sp1", "name": "api", "active": False, "tabs": []},
            {"id": "sp2", "name": None, "active": True, "tabs": []},
        ]})
        # An app home of its own, or this reads the real one: the list of
        # projects that have a browser lives there, and a machine where it
        # names other projects turns this into an empty answer.
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(bridge, "app_home", lambda: Path(directory)), \
                patch.object(bridge, "app_cli", lambda: "/usr/bin/herdr-app"), \
                patch.object(bridge.subprocess, "run",
                             lambda *a, **k: subprocess.CompletedProcess(a, 0, answer, "")):
            self.assertEqual(bridge.app_spaces(), [
                {"workspace_id": "sp1", "label": "api", "focused": False},
                # No name yet: the id, never an empty label, which the strip
                # would show as a group with no title.
                {"workspace_id": "sp2", "label": "sp2", "focused": True},
            ])

    def test_a_space_turned_off_gets_no_group(self):
        """Not every project needs a browser, and a group per space is noise
        when half of them are never opened. The panel writes the choice into
        the app's own shared state; this is the other end of it."""
        answer = json.dumps({"spaces": [
            {"id": "sp1", "name": "api", "active": True, "tabs": []},
            {"id": "sp2", "name": "notes", "active": False, "tabs": []},
        ]})
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(json.dumps({"browsr.skip": {"sp2": True}}))
            with patch.object(bridge, "app_home", lambda: home), \
                    patch.object(bridge, "app_cli", lambda: "/usr/bin/herdr-app"), \
                    patch.object(bridge.subprocess, "run",
                                 lambda *a, **k: subprocess.CompletedProcess(a, 0, answer, "")):
                self.assertEqual([s["label"] for s in bridge.app_spaces()], ["api"])

    def test_turning_a_space_back_on_is_remembered_as_off_being_false(self):
        """The panel removes the key, but an older copy wrote false."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(
                json.dumps({"browsr.skip": {"sp1": False, "sp2": True}}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertEqual(bridge.skipped_spaces(), {"sp2"})

    def test_shared_state_that_will_not_parse_turns_nothing_off(self):
        """It is the app's file, written by every panel. A bad read must not
        silently take the browser away from every project."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text("{ not json")
            with patch.object(bridge, "app_home", lambda: home):
                self.assertEqual(bridge.skipped_spaces(), set())

    def test_a_new_project_starts_without_a_browser(self):
        """A project has one only if it is on the list. A new project — and a
        new worktree — is not, so nothing is made for it until someone says
        so or an agent asks."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(json.dumps({"browsr.on": {"sp1": True}}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertEqual(
                    bridge.spaces_with_a_browser(["sp1", "sp2", "sp3"]), {"sp1"})

    def test_the_old_list_still_reads_the_old_way_round(self):
        """Before the switch the stored list said which projects were turned
        *off*. Until a panel has written the new one, reading it as the new
        one would take the browser away from everything at once."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(json.dumps({"browsr.skip": {"sp2": True}}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertEqual(
                    bridge.spaces_with_a_browser(["sp1", "sp2", "sp3"]), {"sp1", "sp3"})

    def test_turning_one_off_in_the_new_list_is_the_key_going_away(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(
                json.dumps({"browsr.on": {"sp1": True, "sp2": False}}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertEqual(bridge.spaces_with_a_browser(["sp1", "sp2"]), {"sp1"})

    def _home_with(self, directory, shared):
        home = Path(directory)
        (home / "shared.json").write_text(json.dumps(shared))
        return home

    def test_browsr_is_on_unless_the_panel_says_off(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home_with(directory, {})
            with patch.object(bridge, "app_home", lambda: home):
                self.assertTrue(bridge.browsr_switched_on())
            (home / "shared.json").write_text(json.dumps({"powr.modules": {"spacr": False}}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertTrue(bridge.browsr_switched_on())
            (home / "shared.json").write_text(json.dumps({"powr.modules": {"browsr": False}}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertFalse(bridge.browsr_switched_on())

    def test_switched_off_nothing_starts_the_browser_or_a_watcher(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home_with(directory, {"powr.modules": {"browsr": False}})
            with patch.object(bridge, "app_home", lambda: home), \
                    patch.object(bridge, "ensure_browser") as ensure, \
                    patch.object(bridge, "sync_workspaces") as sync, \
                    patch.object(bridge.subprocess, "Popen") as popen, \
                    patch.object(bridge, "herdr_host", return_value="app"):
                for command in (bridge.launch, bridge.sync_and_follow, bridge.start_watcher):
                    with self.assertRaises(RuntimeError) as caught:
                        command()
                    self.assertEqual(str(caught.exception), bridge.BROWSR_OFF)
                ensure.assert_not_called()
                sync.assert_not_called()
                popen.assert_not_called()

    def test_switched_off_ensure_browser_refuses_before_any_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home_with(directory, {"powr.modules": {"browsr": False}})
            with patch.object(bridge, "app_home", lambda: home), \
                    patch.object(bridge, "setup") as setup:
                with self.assertRaises(RuntimeError) as caught:
                    bridge.ensure_browser()
            self.assertEqual(str(caught.exception), bridge.BROWSR_OFF)
            setup.assert_not_called()

    def test_switched_off_an_agent_is_refused_without_a_question(self):
        with tempfile.TemporaryDirectory() as directory, \
                tempfile.TemporaryDirectory() as state:
            home = self._home_with(directory, {"powr.modules": {"browsr": False}})
            ask = Path(state) / "ask.json"
            with patch.object(bridge, "app_home", lambda: home), \
                    patch.object(bridge, "ASK_FILE", ask), \
                    patch.object(bridge, "ensure_browser") as ensure:
                with self.assertRaises(RuntimeError) as caught:
                    bridge.ask_extension("open_url", "sp1", "could not open",
                                         url="http://localhost:3000")
            self.assertEqual(str(caught.exception), bridge.BROWSR_OFF)
            self.assertFalse(ask.exists())
            ensure.assert_not_called()

    def test_off_quits_the_browser_and_every_watcher(self):
        with patch.object(bridge, "stop_watchers") as watchers, \
                patch.object(bridge, "quit_browser", return_value=1) as quit_:
            bridge.switch_off()
        watchers.assert_called_once()
        quit_.assert_called_once()

    def test_the_group_you_are_in_is_the_only_one_wearing_a_colour(self):
        """Twelve grey groups and one marked. The mark rides along with the
        list itself, so there is no moment where it has been greyed and the
        switch has not arrived yet."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(json.dumps({"browsr.accent": "orange"}))
            with patch.object(bridge, "app_home", lambda: home), \
                    patch.object(bridge, "herdr_session_id", return_value="/run/h.sock"):
                message = bridge.workspace_set_message([
                    {"workspace_id": "sp1", "label": "api"},
                    {"workspace_id": "sp2", "label": "notes", "focused": True},
                ])
        self.assertEqual([w["color"] for w in message["workspaces"]], ["grey", "orange"])

    def test_a_switch_carries_the_mark_and_what_the_rest_go_back_to(self):
        """The extension holds no opinion about colour, so both ends of the
        change travel with the switch that causes it."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(json.dumps({"browsr.accent": "cyan"}))
            with patch.object(bridge, "app_home", lambda: home), \
                    patch.object(bridge, "herdr_session_id", return_value="/run/h.sock"):
                message = bridge.focused_message("sp2", "notes")
        self.assertEqual(message["event"], "focused")
        self.assertEqual((message["color"], message["rest"]), ("cyan", "grey"))

    def test_a_switch_says_whether_the_other_groups_fold(self):
        """Folding is the default; the Powr panel can turn it off."""
        for stored, fold in (({}, True), ({"browsr.fold": False}, False)):
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                (home / "shared.json").write_text(json.dumps(stored))
                with patch.object(bridge, "app_home", lambda: home), \
                        patch.object(bridge, "herdr_session_id", return_value="/run/h.sock"):
                    message = bridge.focused_message("sp2", "notes")
            self.assertIs(message["fold"], fold)

    def test_an_accent_chromium_never_heard_of_is_not_sent_to_it(self):
        """Chromium refuses anything outside its nine names, and a refused
        update throws — taking the whole switch down with it."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(
                json.dumps({"browsr.accent": "#faa968"}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertEqual(bridge.accent_color(), "blue")

    def test_a_grey_mark_would_not_be_a_mark(self):
        """Grey is what every other group is wearing."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "shared.json").write_text(json.dumps({"browsr.accent": "grey"}))
            with patch.object(bridge, "app_home", lambda: home):
                self.assertEqual(bridge.accent_color(), "blue")

    def test_restart_finds_every_watcher_and_nothing_else(self):
        """Restart has to replace the watcher as well as the browser, or the
        old code keeps following."""
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            processes = {
                "21": ["/usr/bin/python3", "/x/browsr/bridge.py", "--app", "watch"],
                "22": ["/usr/bin/python3", "/x/browsr/bridge.py", "watch"],
                "23": ["/usr/bin/python3", "/x/browsr/bridge.py", "--app", "status"],
                "24": ["/usr/bin/python3", "/x/browsr/browsr-chrome-mcp.py"],
                "25": ["vim", "watch"],
            }
            for name, argv in processes.items():
                (proc / name).mkdir()
                (proc / name / "cmdline").write_bytes(
                    b"\0".join(a.encode() for a in argv) + b"\0")
            self.assertEqual(bridge.watcher_pids(proc), [21, 22])

    def test_an_app_that_will_not_answer_is_not_a_user_with_no_spaces(self):
        """The extension reads an empty list as "say nothing" on purpose."""
        with patch.object(bridge, "app_cli", lambda: "/usr/bin/herdr-app"), \
                patch.object(bridge.subprocess, "run",
                             lambda *a, **k: subprocess.CompletedProcess(a, 0, "{}", "")):
            self.assertIsNone(bridge.app_spaces())

    def test_a_caller_in_an_app_terminal_finds_its_own_space(self):
        """The app hands out a terminal address where the other Herdr hands
        out a workspace id, so the space has to be looked up."""
        answer = json.dumps({"spaces": [
            {"id": "sp1", "name": "api", "tabs": [{"terminals": [{"terminal": "t1"}]}]},
            {"id": "sp2", "name": "docs", "tabs": [{"terminals": [{"terminal": "t8"}]}]},
        ]})
        with patch.object(bridge, "app_cli", lambda: "/usr/bin/herdr-app"), \
                patch.object(bridge.subprocess, "run",
                             lambda *a, **k: subprocess.CompletedProcess(a, 0, answer, "")):
            self.assertEqual(bridge.app_space_of_terminal("t8"), "sp2")
            self.assertIsNone(bridge.app_space_of_terminal("t99"))

    def test_the_space_in_front_is_followed_once_per_change(self):
        """The app rewrites view.json on every switch. Browsr may act on the
        change and on nothing else: syncing on every tick would re-send the
        whole list five times a second."""
        with tempfile.TemporaryDirectory() as directory:
            view = Path(directory) / "view.json"
            view.write_text(json.dumps({"active": "sp1"}))
            synced = []
            with patch.object(bridge, "app_view_file", lambda: view), \
                    patch.object(bridge, "sync_workspaces", lambda: synced.append(1)), \
                    patch.object(bridge, "_VIEW_STAMP", None):
                space = bridge.app_focused_space()
                for _ in range(5):
                    space = bridge.focus_step(space)
                self.assertEqual(synced, [], "followed a space that never changed")

                time.sleep(0.01)
                view.write_text(json.dumps({"active": "sp2"}))
                for _ in range(5):
                    space = bridge.focus_step(space)

            self.assertEqual(len(synced), 1, "one switch, one sync")
            self.assertEqual(space, "sp2")

    def test_each_herdr_gets_its_own_follower(self):
        """One browser, so one health watch; two Herdrs, so two followers.
        They shared a lock, and the Herdr that started second was followed by
        nobody: switching spaces in the desktop app moved no tabs at all."""
        with patch.object(bridge, "HOST_OVERRIDE", "app"):
            under_app = bridge.focus_lock_path()
        with patch.object(bridge, "HOST_OVERRIDE", "tui"):
            under_tui = bridge.focus_lock_path()
        self.assertNotEqual(under_app, under_tui)

    def test_the_other_herdrs_health_watch_does_not_stop_the_following(self):
        """The regression itself: the terminal Herdr's watcher holds the
        health lock for days. A watcher started under the app must still
        follow the app's spaces, and simply leave the reporting to it."""
        with tempfile.TemporaryDirectory() as directory:
            health = Path(directory) / "health.lock"
            focus = Path(directory) / "focus-app.lock"
            taken = health.open("w")
            fcntl.flock(taken, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch.object(bridge, "HOST_OVERRIDE", "app"):
                    following, checking, held = bridge.take_watch_locks(health, focus)
                self.assertTrue(following, "gave up the following over the health lock")
                self.assertFalse(checking, "two watchers reporting on one browser")
                for handle in held:
                    handle.close()
            finally:
                taken.close()

    def test_a_second_watcher_for_the_same_herdr_does_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            health = Path(directory) / "health.lock"
            focus = Path(directory) / "focus-app.lock"
            with patch.object(bridge, "HOST_OVERRIDE", "app"):
                first = bridge.take_watch_locks(health, focus)
                self.assertEqual(first[:2], (True, True))
                second = bridge.take_watch_locks(health, focus)
            self.assertEqual(second[:2], (False, False))
            for handle in first[2]:
                handle.close()

    def test_the_panel_is_told_when_nothing_is_following(self):
        """The browser outlives any one Herdr, so it can be up and answering
        while the part that follows the spaces is gone. That looks like
        nothing at all to someone switching spaces."""
        with tempfile.TemporaryDirectory() as directory:
            focus = Path(directory) / "focus-app.lock"
            with patch.object(bridge, "HOST_OVERRIDE", "app"), \
                    patch.object(bridge, "focus_lock_path", lambda: focus):
                self.assertFalse(bridge.following_now())
                handle = focus.open("w")
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    self.assertTrue(bridge.following_now())
                finally:
                    handle.close()

    def test_the_question_waits_for_an_answer_and_a_yes_lets_the_page_through(self):
        """Refusing outright was right but unhelpful: the agent stops, and the
        one person who can change it has to be told, in another window, what
        to press. The question goes on screen instead."""
        with tempfile.TemporaryDirectory() as directory:
            ask = Path(directory) / "ask.json"
            answer = Path(directory) / "answer.json"

            def say_yes():
                for _ in range(200):
                    if ask.exists():
                        asked = json.loads(ask.read_text())
                        answer.write_text(json.dumps({"id": asked["id"], "yes": True}))
                        return
                    time.sleep(0.02)

            with patch.object(bridge, "HOST_OVERRIDE", "app"), \
                    patch.object(bridge, "STATE_DIR", Path(directory)), \
                    patch.object(bridge, "ASK_FILE", ask), \
                    patch.object(bridge, "ANSWER_FILE", answer), \
                    patch.object(bridge, "spaces_with_a_browser", lambda known=None: set()), \
                    patch.object(bridge, "space_name", lambda _id: "brain"):
                threading.Thread(target=say_yes, daemon=True).start()
                bridge.ask_to_switch_on("sp9", "http://localhost:3000")

            asked = json.loads(ask.read_text())
            self.assertEqual(asked["space"], "sp9")
            self.assertEqual(asked["name"], "brain", "asked about a project by its id")
            self.assertEqual(asked["url"], "http://localhost:3000")

    def test_a_no_on_screen_is_a_refusal_with_the_answer_in_it(self):
        with tempfile.TemporaryDirectory() as directory:
            ask = Path(directory) / "ask.json"
            answer = Path(directory) / "answer.json"

            def say_no():
                for _ in range(200):
                    if ask.exists():
                        asked = json.loads(ask.read_text())
                        answer.write_text(json.dumps({"id": asked["id"], "yes": False}))
                        return
                    time.sleep(0.02)

            with patch.object(bridge, "HOST_OVERRIDE", "app"), \
                    patch.object(bridge, "STATE_DIR", Path(directory)), \
                    patch.object(bridge, "ASK_FILE", ask), \
                    patch.object(bridge, "ANSWER_FILE", answer), \
                    patch.object(bridge, "spaces_with_a_browser", lambda known=None: set()), \
                    patch.object(bridge, "space_name", lambda _id: "brain"):
                threading.Thread(target=say_no, daemon=True).start()
                with self.assertRaises(RuntimeError) as refused:
                    bridge.ask_to_switch_on("sp9", "http://localhost:3000")
            self.assertIn("the answer on screen was no", str(refused.exception))

    def test_nobody_at_the_screen_is_not_a_hang(self):
        """An agent asking while nobody is there gets on with its life, with a
        sentence that says what to do."""
        with tempfile.TemporaryDirectory() as directory:
            ask = Path(directory) / "ask.json"
            with patch.object(bridge, "HOST_OVERRIDE", "app"), \
                    patch.object(bridge, "STATE_DIR", Path(directory)), \
                    patch.object(bridge, "ASK_FILE", ask), \
                    patch.object(bridge, "ANSWER_FILE", Path(directory) / "answer.json"), \
                    patch.object(bridge, "ASK_SECONDS", 0.4), \
                    patch.object(bridge, "spaces_with_a_browser", lambda known=None: set()), \
                    patch.object(bridge, "space_name", lambda _id: "brain"):
                with self.assertRaises(RuntimeError) as refused:
                    bridge.ask_to_switch_on("sp9", None)
            self.assertIn("Nobody answered", str(refused.exception))
            self.assertFalse(ask.exists(), "left a question nobody is waiting on")

    def test_a_page_is_not_opened_into_a_project_that_is_switched_off(self):
        """Opening a page in a project that is off made a group the next
        project switch took away, with an agent working in those tabs. It
        asks first, and with no answer it never gets as far as the browser."""
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bridge, "HOST_OVERRIDE", "app"), \
                    patch.object(bridge, "STATE_DIR", Path(directory)), \
                    patch.object(bridge, "ASK_FILE", Path(directory) / "ask.json"), \
                    patch.object(bridge, "ANSWER_FILE", Path(directory) / "answer.json"), \
                    patch.object(bridge, "ASK_SECONDS", 0.3), \
                    patch.object(bridge, "space_name", lambda _id: "brain"), \
                    patch.object(bridge, "spaces_with_a_browser", lambda known=None: set()), \
                    patch.object(bridge, "ensure_browser",
                                 lambda **kw: self.fail("started the browser for a project that is off")):
                with self.assertRaises(RuntimeError) as refused:
                    bridge.ask_extension("open_url", "sp9", "nope", url="http://localhost:3000")
            self.assertIn("switched off", str(refused.exception))
            self.assertIn("browser mark", str(refused.exception), "said no without saying where the switch is")

    def test_reading_the_tabs_of_a_project_that_is_off_is_still_allowed(self):
        """Only making a group is refused. Asking what is there takes nothing
        away, and an agent that has just been refused should be able to look."""
        seen = []
        with patch.object(bridge, "HOST_OVERRIDE", "app"), \
                patch.object(bridge, "spaces_with_a_browser", lambda known=None: set()), \
                patch.object(bridge, "ensure_browser", lambda **kw: seen.append("started")), \
                patch.object(bridge, "socket_request",
                             lambda *a, **k: {"ok": True, "result": {"tabs": []}}):
            bridge.ask_extension("workspace_tabs", "sp9", "nope")
        self.assertEqual(seen, ["started"])

    def test_a_missing_view_file_is_not_a_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "gone.json"
            with patch.object(bridge, "app_view_file", lambda: missing), \
                    patch.object(bridge, "sync_workspaces",
                                 lambda: self.fail("synced without a view file")):
                self.assertIsNone(bridge.app_focused_space())
                self.assertEqual(bridge.focus_step("sp1"), "sp1")

    def test_the_browser_is_found_under_the_name_each_distribution_uses(self):
        """Only some distributions call it `chromium`.

        The first version looked for that one name — the one this machine
        uses. On Debian and Ubuntu the same browser is `chromium-browser`, so
        those users were told to install a browser they already had, by the
        first command the README hands them.
        """
        for name in ("chromium", "chromium-browser", "google-chrome-stable", "chrome"):
            with patch.dict(os.environ), \
                    patch("shutil.which", lambda asked, want=name: f"/usr/bin/{asked}" if asked == want else None):
                os.environ.pop("BROWSR_CHROMIUM", None)
                self.assertEqual(bridge.find_chromium(), f"/usr/bin/{name}", name)

    def test_a_browser_in_an_unguessable_place_can_be_pointed_at(self):
        with patch.dict(os.environ, {"BROWSR_CHROMIUM": "/opt/odd/browser"}), \
                patch("shutil.which", lambda asked: asked if asked == "/opt/odd/browser" else None):
            self.assertEqual(bridge.find_chromium(), "/opt/odd/browser")

    def test_no_browser_at_all_says_which_names_were_tried(self):
        """The message is the whole fix for someone whose binary is named
        differently again, so it has to carry the list and the way out."""
        with patch.dict(os.environ), patch("shutil.which", lambda asked: None):
            os.environ.pop("BROWSR_CHROMIUM", None)
            self.assertIsNone(bridge.find_chromium())
            with self.assertRaises(RuntimeError) as caught:
                bridge.setup()

        message = str(caught.exception)
        self.assertIn("chromium-browser", message)
        self.assertIn("BROWSR_CHROMIUM", message)

    def test_the_search_really_walks_PATH(self):
        """The other three tests fake the lookup, so none of them proves the
        real one works. This drives it: a real executable, a real PATH, no
        patching of the thing under test."""
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "chromium-browser"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)

            with patch.dict(os.environ, {"PATH": directory}):
                os.environ.pop("BROWSR_CHROMIUM", None)
                self.assertEqual(bridge.find_chromium(), str(fake))

                os.environ["BROWSR_CHROMIUM"] = str(fake)
                self.assertEqual(bridge.find_chromium(), str(fake))

    def test_a_wrong_override_is_not_reported_as_a_missing_browser(self):
        with patch.dict(os.environ, {"BROWSR_CHROMIUM": "/opt/typo"}), \
                patch("shutil.which", lambda asked: None):
            with self.assertRaises(RuntimeError) as caught:
                bridge.setup()

        self.assertIn("/opt/typo", str(caught.exception))

    def test_an_edited_extension_is_not_run_from_the_profile_cache(self):
        """Chromium keeps its own compiled copy of the service worker.

        A browser restart runs that copy again, and so does a version bump in
        the manifest, so an edited extension can go on running its old code
        while the repository looks fixed. Verified on the installed Chromium
        152: only dropping the profile's service-worker registration makes it
        read the files again.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root / "extension"
            extension.mkdir()
            (extension / "service-worker.js").write_text("first")
            cache = root / "profile" / "Default" / "Service Worker"
            cache.mkdir(parents=True)

            with patch.object(bridge, "EXTENSION_DIR", extension), \
                    patch.object(bridge, "PROFILE_DIR", root / "profile"), \
                    patch.object(bridge, "EXTENSION_STAMP", root / "extension.stamp"):
                self.assertTrue(bridge.refresh_extension_if_changed())
                self.assertFalse(cache.exists())

                cache.mkdir(parents=True)
                self.assertFalse(bridge.refresh_extension_if_changed())
                self.assertTrue(cache.exists(), "an untouched extension was reloaded for nothing")

                (extension / "service-worker.js").write_text("second and longer")
                self.assertTrue(bridge.refresh_extension_if_changed())
                self.assertFalse(cache.exists())

    def test_only_one_process_at_a_time_may_start_the_browser(self):
        """Several callers wake at once after a reboot; only one may launch."""
        with sandboxed_state() as root:
            lock = root / "state" / "launch.lock"
            probe = (
                "import fcntl,sys\n"
                f"handle = open({str(lock)!r}, 'w')\n"
                "try:\n"
                "    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "except OSError:\n"
                "    sys.exit(3)\n"
                "sys.exit(0)\n"
            )
            with bridge.launch_lock():
                held = subprocess.run([sys.executable, "-c", probe])
            free = subprocess.run([sys.executable, "-c", probe])

        self.assertEqual(held.returncode, 3, "a second process got in while one was launching")
        self.assertEqual(free.returncode, 0, "the lock was not released")


class LinkTests(unittest.TestCase):
    """Links clicked in Herdr go to Browsr; every other link goes where it went."""

    HYPRLAND = ("[Default Applications]\n"
                "x-scheme-handler/http=omarchy-browser.desktop;google-chrome.desktop\n"
                "x-scheme-handler/https=re.sonny.Junction.desktop\n")
    GENERAL = ("[Default Applications]\n"
               "text/html=chromium.desktop\n"
               "x-scheme-handler/https=chromium.desktop\n"
               "[Added Associations]\n"
               "x-scheme-handler/http=firefox.desktop;\n")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "state" / "browsr").mkdir(parents=True)
        self.config = root / "config"
        self.config.mkdir()
        (self.config / "hyprland-mimeapps.list").write_text(self.HYPRLAND)
        (self.config / "mimeapps.list").write_text(self.GENERAL)
        self.patches = [
            patch.object(bridge, "STATE_DIR", root / "state" / "browsr"),
            patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data"),
                                    "XDG_CONFIG_HOME": str(self.config),
                                    "XDG_CURRENT_DESKTOP": "Hyprland"}),
            patch.object(bridge.shutil, "which", lambda name: f"/usr/bin/{name}"),
            patch.object(bridge, "browser_now", lambda: "google-chrome.desktop"),
            patch.object(bridge, "default_handler", lambda kind: "chromium.desktop"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_links_go_first_in_every_list_and_come_back_exactly(self):
        bridge.links_on()
        for name in ("hyprland-mimeapps.list", "mimeapps.list"):
            defaults = bridge.read_defaults(self.config / name)
            for kind in bridge.LINK_TYPES:
                self.assertTrue(defaults[kind].startswith("powr-links.desktop;"), (name, kind))
        self.assertIn("powr-open", bridge.links_desktop_file().read_text())
        bridge.links_on()  # a second time must not remember itself as "before"
        bridge.links_off()
        self.assertEqual((self.config / "hyprland-mimeapps.list").read_text(), self.HYPRLAND)
        self.assertEqual((self.config / "mimeapps.list").read_text(), self.GENERAL)
        self.assertFalse(bridge.links_desktop_file().exists())

    def test_the_old_browser_survives_cleanup_of_the_state_directory(self):
        bridge.links_on()
        self.assertNotIn(bridge.STATE_DIR, bridge.links_saved_file().parents)

    def decide(self, url, front=True, on=True, space="sp1", spaces=("sp1",), alive=True):
        groups = [{"workspace_id": s, "label": s, "focused": s == space} for s in spaces]
        with patch.object(bridge, "herdr_in_front", lambda: front), \
                patch.object(bridge, "browsr_switched_on", lambda: on), \
                patch.object(bridge, "app_spaces", lambda: groups), \
                patch.object(bridge, "bridge_alive", lambda: alive):
            return bridge.link_goes_to_browsr(url)

    def test_a_link_clicked_in_herdr_goes_to_the_space_in_front(self):
        self.assertEqual(self.decide("https://example.com/a"), "sp1")
        self.assertEqual(self.decide("http://localhost:3000"), "sp1")

    def test_every_other_link_goes_to_the_old_browser(self):
        self.assertIsNone(self.decide("https://example.com", front=False))
        self.assertIsNone(self.decide("https://example.com", on=False))
        self.assertIsNone(self.decide("https://example.com", spaces=()))
        self.assertIsNone(self.decide("https://example.com", alive=False))
        self.assertIsNone(self.decide("mailto:someone@example.com"))
        self.assertIsNone(self.decide("file:///etc/passwd"))

    def test_a_link_browsr_cannot_take_still_opens(self):
        bridge.links_on()
        launched = []
        with patch.object(bridge, "link_goes_to_browsr", lambda url: "sp1"), \
                patch.object(bridge, "ask_extension", side_effect=RuntimeError("down")), \
                patch.object(bridge.subprocess, "Popen", lambda args, **_: launched.append(args)):
            bridge.handle_link("https://example.com")
        self.assertEqual(launched, [["/usr/bin/gtk-launch", "chromium.desktop", "https://example.com"]])

    def test_the_open_the_browser_key_opens_the_old_browser(self):
        bridge.links_on()
        launched = []
        with patch.object(bridge.subprocess, "Popen", lambda args, **_: launched.append(args)):
            bridge.handle_link("")
        self.assertEqual(launched, [["/usr/bin/gtk-launch", "google-chrome.desktop"]])

    def test_http_links_go_where_http_links_went(self):
        bridge.links_on()
        launched = []
        with patch.object(bridge, "link_goes_to_browsr", lambda url: None), \
                patch.object(bridge.subprocess, "Popen", lambda args, **_: launched.append(args)):
            bridge.handle_link("http://example.com")
        self.assertEqual(launched, [["/usr/bin/gtk-launch", "google-chrome.desktop", "http://example.com"]])

    def test_a_clicked_link_opens_in_front_in_browsr(self):
        asked = []
        with patch.object(bridge, "link_goes_to_browsr", lambda url: "sp1"), \
                patch.object(bridge, "ask_extension",
                             lambda kind, space, failure, **extra: asked.append((kind, space, extra))), \
                patch.object(bridge, "show_window", lambda: True):
            bridge.handle_link("https://example.com")
        self.assertEqual(asked, [("open_tab", "sp1", {"url": "https://example.com", "focus": True})])


class TabGroupTests(unittest.TestCase):
    """A space with several tabs gets a group per tab, named space:tab."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patch = patch.object(bridge, "STATE_DIR", Path(self.directory.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    @staticmethod
    def space(tabs, active="tb1"):
        return {"id": "sp1", "name": "api", "active": True,
                "tabs": [{"id": t, "name": n, "active": t == active,
                          "terminals": [{"terminal": f"term-{t}"}]} for t, n in tabs]}

    def test_one_tab_is_one_group_named_after_the_space(self):
        self.assertEqual(bridge.app_groups([self.space([("tb1", "1")])]), [
            {"workspace_id": "sp1", "label": "api", "focused": True},
        ])

    def test_a_second_tab_splits_the_space_and_the_first_keeps_its_group(self):
        bridge.app_groups([self.space([("tb1", "1")])])
        groups = bridge.app_groups([self.space([("tb1", "1"), ("tb2", "review")], active="tb2")])
        self.assertEqual(groups, [
            {"workspace_id": "sp1", "label": "api:1", "focused": False},
            {"workspace_id": "sp1:tb2", "label": "api:review", "focused": True},
        ])

    def test_closing_the_first_tab_never_hands_its_group_to_another(self):
        bridge.app_groups([self.space([("tb1", "1"), ("tb2", "2")])])
        groups = bridge.app_groups([self.space([("tb2", "2")], active="tb2")])
        self.assertEqual(groups, [{"workspace_id": "sp1:tb2", "label": "api", "focused": True}])

    def test_an_agent_finds_the_group_of_its_own_tab(self):
        listing = [self.space([("tb1", "1"), ("tb2", "2")])]
        with patch.object(bridge, "app_listing", lambda: listing):
            self.assertEqual(bridge.app_key_of_terminal("term-tb1"), "sp1")
            self.assertEqual(bridge.app_key_of_terminal("term-tb2"), "sp1:tb2")
            self.assertIsNone(bridge.app_key_of_terminal("term-none"))

    def test_a_tab_key_belongs_to_its_space(self):
        self.assertEqual(bridge.space_of_key("sp1:tb2"), "sp1")
        self.assertEqual(bridge.space_of_key("sp1"), "sp1")

    def test_switching_tabs_is_a_change_to_follow(self):
        view = Path(self.directory.name) / "view.json"
        view.write_text(json.dumps({"active": "sp1", "spaces": {"sp1": "tb1"}}))
        with patch.object(bridge, "app_view_file", lambda: view):
            before = bridge.app_focused_where()
            view.write_text(json.dumps({"active": "sp1", "spaces": {"sp1": "tb2"}}))
            self.assertNotEqual(bridge.app_focused_where(), before)


def fake_system_log(kernel="", journal=(), coredumpctl=""):
    """Stand-in for the one seam that reads the system's logs.

    `journal` is a list of coredump records as journalctl prints them with
    `-o json`; the kernel log is plain text.
    """
    calls = []

    def read(argv):
        calls.append(list(argv))
        if argv[0] == "coredumpctl":
            return coredumpctl
        if "-k" in argv:
            return kernel
        if "--user" in argv:
            return "\n".join(json.dumps(record) for record in journal)
        return ""
    read.calls = calls
    return read


def dump_record(pid, signal_name, cmdline):
    return {
        "COREDUMP_PID": str(pid),
        "COREDUMP_SIGNAL_NAME": signal_name,
        "COREDUMP_SIGNAL": "11",
        "COREDUMP_TIMESTAMP": "1794489292000000",
        "COREDUMP_EXE": "/usr/lib/chromium/chromium",
        "COREDUMP_CMDLINE": cmdline,
        "MESSAGE": f"Process {pid} (chromium) of user 1000 dumped core.",
    }


class CrashLogTests(unittest.TestCase):
    """Browsr's browser goes down and nothing used to say why."""

    NOW = 1794563000.0  # 13 Nov 2026, late morning in Europe

    def run_watch(self, polls, system_log):
        """Feed crash_step a sequence of browser pid lists, ten seconds apart."""
        state = None
        now = self.NOW
        for pids in polls:
            with patch.object(bridge, "browser_pids", return_value=pids), \
                    patch.object(bridge, "system_log", system_log):
                state = bridge.crash_step(state, now)
            now += bridge.HEALTH_POLL_SECONDS
        return state

    def entries(self):
        path = bridge.crash_log_file()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_launch_keeps_the_previous_log_and_only_the_newest_ten(self):
        with sandboxed_state() as root, \
                patch.object(bridge, "refresh_extension_if_changed", lambda: False), \
                patch.object(bridge.subprocess, "Popen", lambda *a, **k: None):
            log = root / "state" / "chromium.log"
            for run in range(12):
                log.write_text(f"run {run}\n")
                os.utime(log, (self.NOW + run * 60, self.NOW + run * 60))
                bridge.spawn_browser("/usr/bin/chromium")
            kept = sorted((root / "state" / "logs").iterdir())
            self.assertEqual(len(kept), 10, [p.name for p in kept])
            self.assertTrue(all(p.name.startswith("chromium-") and p.suffix == ".log" for p in kept))
            self.assertEqual(kept[0].read_text(), "run 2\n")
            self.assertEqual(kept[-1].read_text(), "run 11\n")
            self.assertEqual(log.read_text(), "", "the new launch did not get a fresh log")

    def test_a_deliberate_stop_is_not_a_crash(self):
        with sandboxed_state():
            state = self.run_watch([[100]], fake_system_log())
            with patch.object(bridge, "browser_pids", return_value=[100]), \
                    patch.object(bridge.os, "kill", lambda *a: None), \
                    patch.object(bridge.time, "time", return_value=self.NOW + 5):
                bridge.quit_browser()
            with patch.object(bridge, "browser_pids", return_value=[]), \
                    patch.object(bridge, "system_log", fake_system_log()):
                bridge.crash_step(state, self.NOW + 10)
            self.assertEqual(self.entries(), [])

    def test_an_old_stop_mark_does_not_hide_a_later_crash(self):
        with sandboxed_state():
            with patch.object(bridge, "browser_pids", return_value=[100]), \
                    patch.object(bridge.os, "kill", lambda *a: None), \
                    patch.object(bridge.time, "time", return_value=self.NOW - 3600):
                bridge.quit_browser()
            self.run_watch([[100], []], fake_system_log())
            self.assertEqual(len(self.entries()), 1)

    def test_out_of_memory(self):
        kernel = ("Out of memory: Killed process 100 (chromium) total-vm:1540520424kB, "
                  "anon-rss:602388kB, UID:1000 pgtables:4728kB oom_score_adj:300\n")
        with sandboxed_state():
            self.run_watch([[100], []], fake_system_log(kernel=kernel))
            [entry] = self.entries()
            self.assertEqual(entry["pid"], 100)
            self.assertEqual(entry["how"], "killed: out of memory")

    def test_a_core_dump_names_the_signal(self):
        with sandboxed_state() as root:
            cmdline = f"$'/usr/lib/chromium/chromium --user-data-dir={root / 'profile'} --no-first-run'"
            log = fake_system_log(journal=[dump_record(100, "SIGSEGV", cmdline)])
            self.run_watch([[100], []], log)
            [entry] = self.entries()
            self.assertEqual(entry["how"], "crashed: SIGSEGV")

    def test_a_dump_that_arrives_late_corrects_the_entry(self):
        """systemd-coredump took almost two minutes to record a 60 MB dump
        here, so the browser is gone well before the evidence is in."""
        with sandboxed_state() as root:
            cmdline = f"/usr/lib/chromium/chromium --user-data-dir={root / 'profile'}"
            records = []
            log = fake_system_log(journal=records)
            state = self.run_watch([[100], []], log)
            self.assertEqual([e["how"] for e in self.entries()], ["closed or ended without a trace"])
            records.append(dump_record(100, "SIGTRAP", cmdline))
            with patch.object(bridge, "browser_pids", return_value=[]), \
                    patch.object(bridge, "system_log", log):
                bridge.crash_step(state, self.NOW + 90)
            self.assertEqual([e["how"] for e in self.entries()], ["crashed: SIGTRAP"])

    def test_no_trace_keeps_the_end_of_the_browser_log(self):
        with sandboxed_state() as root:
            (root / "state" / "chromium.log").write_text(
                "".join(f"line {n}\n" for n in range(100)))
            self.run_watch([[100], []], fake_system_log())
            [entry] = self.entries()
            self.assertEqual(entry["how"], "closed or ended without a trace")
            self.assertEqual(len(entry["log"]), 40)
            self.assertEqual(entry["log"][-1], "line 99")

    def test_a_tab_crash_is_logged_once(self):
        with sandboxed_state() as root:
            ours = f"$'/usr/lib/chromium/chromium --type=renderer --user-data-dir={root / 'profile'} --lang=en-US'"
            theirs = "$'/usr/lib/chromium/chromium --type=renderer --user-data-dir=/elsewhere --lang=en-US'"
            log = fake_system_log(journal=[dump_record(555, "SIGSEGV", ours),
                                           dump_record(556, "SIGSEGV", theirs)])
            self.run_watch([[100], [100], [100]], log)
            [entry] = self.entries()
            self.assertEqual(entry["how"], "tab crashed: SIGSEGV")
            self.assertEqual(entry["pid"], 555)
            # A watcher started afresh does not log it a second time.
            self.run_watch([[100], [100]], log)
            self.assertEqual(len(self.entries()), 1)

    def test_missing_tools_mean_no_trace_and_no_failure(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError("journalctl")

        def slow(*args, **kwargs):
            raise subprocess.TimeoutExpired("journalctl", 3)

        for broken in (missing, slow):
            with sandboxed_state(), patch.object(bridge.subprocess, "run", broken):
                self.assertEqual(bridge.system_log(["journalctl", "-k"]), "")
                state = None
                for now, pids in ((self.NOW, [100]), (self.NOW + 10, [])):
                    with patch.object(bridge, "browser_pids", return_value=pids):
                        state = bridge.crash_step(state, now)
                self.assertEqual([e["how"] for e in self.entries()],
                                 ["closed or ended without a trace"])

    def write_entries(self, entries):
        bridge.crash_log_file().write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries))

    def at(self, day, hour, minute):
        import datetime
        return datetime.datetime(2026, 11, day, hour, minute).astimezone().isoformat(timespec="seconds")

    def test_the_crashes_command(self):
        with sandboxed_state():
            entries = [{"at": self.at(1 + n, 9, 0), "pid": n, "kind": "browser",
                        "how": "closed or ended without a trace"} for n in range(11)]
            entries.append({"at": self.at(13, 10, 42), "pid": 99, "kind": "browser",
                            "how": "killed: out of memory"})
            self.write_entries(entries)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                bridge.print_crashes()
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[9], "13 Nov 10:42 — killed: out of memory")
            self.assertEqual(lines[0], "3 Nov 09:00 — closed or ended without a trace")
            self.assertIn(str(bridge.crash_log_file()), lines[-1])

    def test_the_crashes_command_with_nothing_logged(self):
        with sandboxed_state():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                bridge.print_crashes()
            self.assertIn("No crashes", out.getvalue())

    def test_status_says_when_it_last_went_down(self):
        import datetime
        with sandboxed_state():
            now = datetime.datetime(2026, 11, 13, 18, 0).timestamp()
            self.write_entries([{"at": self.at(13, 10, 42), "pid": 1, "kind": "browser",
                                 "how": "killed: out of memory"}])
            self.assertEqual(bridge.last_down(now), "Last went down 10:42 — out of memory.")
            self.assertIsNone(bridge.last_down(now + 86400))
            self.write_entries([{"at": self.at(13, 10, 42), "pid": 1, "kind": "tab",
                                 "how": "tab crashed: SIGSEGV"}])
            self.assertEqual(bridge.last_down(now), "A tab crashed 10:42 — SIGSEGV.")
            with patch.object(bridge, "herdr_workspaces", return_value=[]), \
                    patch.object(bridge, "bridge_alive", return_value=False), \
                    patch.object(bridge, "browser_alive", return_value=False), \
                    patch.object(bridge, "following_now", return_value=False):
                self.assertIn("last_down", bridge.browser_status())


if __name__ == "__main__":
    unittest.main()
