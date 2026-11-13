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
        self.assertLess(out.index(MENU), out.index(CLICK))

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


if __name__ == "__main__":
    unittest.main()
