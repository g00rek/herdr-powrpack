import subprocess
import tempfile
import unittest
from pathlib import Path

import agents_patch

FIXTURE = (Path(__file__).parent / "fixtures" / "agents-main.min.js").read_text()
IMPORT = 'import { switchrBadge, switchrMenu } from "./switchr.js";'
BADGE = '.when(Boolean(switchrBadge(a)), el => el.child(div().text_size(10).text_color(t.dim).child(switchrBadge(a))))'
MENU = '.on_mouse_down("right", (_e, cx) => { cx.stop_propagation(); switchrMenu(a); })'
CLICK = '.on_click(() => run("pane.focus", { pane: a.pane }))'


class PatchTests(unittest.TestCase):
    def test_patch_adds_import_badge_and_menu(self):
        out = agents_patch.patch(FIXTURE)
        lines = out.splitlines()
        last_import = max(i for i, l in enumerate(lines) if l.startswith("import "))
        self.assertEqual(lines[last_import].strip(), IMPORT)
        self.assertEqual(out.count(IMPORT), 1)
        machine = out.index("child(machine)))")
        self.assertEqual(out.count(BADGE), 1)
        self.assertGreater(out.index(BADGE), machine)
        self.assertEqual(out.count(MENU), 1)
        self.assertLess(out.index(".on_hover("), out.index(MENU))
        self.assertEqual(out.index(CLICK, out.index(MENU)), out.index(MENU) + len(MENU) + 9)

    def test_patch_is_idempotent(self):
        once = agents_patch.patch(FIXTURE)
        self.assertEqual(agents_patch.patch(once), once)

    def test_patch_reports_missing_anchor(self):
        for anchor in (CLICK, "child(machine)))"):
            with self.assertRaises(agents_patch.PatchError) as ctx:
                agents_patch.patch(FIXTURE.replace(anchor, "// gone"))
            self.assertIn("anchor", str(ctx.exception))

    def test_patch_refuses_an_anchor_that_appears_twice(self):
        with self.assertRaises(agents_patch.PatchError):
            agents_patch.patch(FIXTURE + FIXTURE)

    def test_patch_refuses_source_without_imports(self):
        body = "\n".join(l for l in FIXTURE.splitlines() if not l.startswith("import "))
        with self.assertRaises(agents_patch.PatchError):
            agents_patch.patch(body)


class MakeCopyTests(unittest.TestCase):
    def setUp(self):
        agents_patch.POLL = 0.01
        agents_patch.WAIT = 0.5
        self.addCleanup(setattr, agents_patch, "POLL", 0.2)
        self.addCleanup(setattr, agents_patch, "WAIT", 10.0)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        (self.home / "mods").mkdir()
        self.target = self.home / "mods" / "switchr-agents"

    def old_copy(self):
        self.target.mkdir()
        (self.target / "main.js").write_text("OLD WORKING COPY")
        (self.target / "mod.json").write_text("{}")

    def fake(self, clone=0, check=0, source=FIXTURE, boom=None):
        def run(*args):
            if boom:
                raise boom
            if args[:2] == ("do", "mod.clone"):
                if clone == 0:
                    self.target.mkdir(parents=True)
                    (self.target / "main.js").write_text(source)
                    (self.target / "mod.json").write_text("{}")
                return subprocess.CompletedProcess(args, clone, "", "clone failed" if clone else "")
            return subprocess.CompletedProcess(args, check, "", "bad mod" if check else "")
        return run

    def assert_old_restored(self):
        self.assertEqual((self.target / "main.js").read_text(), "OLD WORKING COPY")
        self.assertEqual((self.target / "mod.json").read_text(), "{}")
        self.assertFalse((self.home / "switchr-agents.prev").exists())
        self.assertFalse((self.target / "switchr.js").exists())

    def test_success_replaces_old_and_removes_set_aside(self):
        self.old_copy()
        ok, line = agents_patch.make_copy(self.home, self.fake())
        self.assertTrue(ok, line)
        self.assertIn(IMPORT, (self.target / "main.js").read_text())
        self.assertTrue((self.target / "switchr.js").exists())
        self.assertFalse((self.home / "switchr-agents.prev").exists())

    def test_clone_failure_restores_old_copy(self):
        self.old_copy()
        ok, line = agents_patch.make_copy(self.home, self.fake(clone=1))
        self.assertFalse(ok)
        self.assert_old_restored()

    def test_patch_error_restores_old_copy(self):
        self.old_copy()
        ok, line = agents_patch.make_copy(self.home, self.fake(source="// nothing to patch"))
        self.assertFalse(ok)
        self.assertIn("not available with this Herdr version", line)
        self.assert_old_restored()

    def test_check_failure_restores_old_copy(self):
        self.old_copy()
        ok, line = agents_patch.make_copy(self.home, self.fake(check=1))
        self.assertFalse(ok)
        self.assert_old_restored()

    def test_exception_restores_old_copy(self):
        self.old_copy()
        ok, line = agents_patch.make_copy(self.home, self.fake(boom=FileNotFoundError("herdr-app")))
        self.assertFalse(ok)
        self.assert_old_restored()

    def test_copy_that_never_appears_restores_old_copy(self):
        self.old_copy()
        def run(*args):
            return subprocess.CompletedProcess(args, 0, "", "")
        ok, line = agents_patch.make_copy(self.home, run)
        self.assertFalse(ok)
        self.assert_old_restored()

    def test_failure_without_prior_copy_leaves_no_folder(self):
        for fake in (self.fake(clone=1), self.fake(check=1), self.fake(source="x"), self.fake(boom=OSError("x"))):
            ok, line = agents_patch.make_copy(self.home, fake)
            self.assertFalse(ok)
            self.assertFalse(self.target.exists())
            self.assertFalse((self.home / "switchr-agents.prev").exists())


if __name__ == "__main__":
    unittest.main()
