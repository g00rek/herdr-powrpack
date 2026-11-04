# Powr modules and control panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Powr button and panel in the Herdr app that switches the pack's modules (Browsr, Spacr) on and off and shows each module's page.

**Architecture:** A shared value `powr.modules` in the app's shared state is the single switch, read by the mods (JavaScript, Herdr's gpui-kit mod API) and by `bridge.py` (Python). A new mod `powr` draws the button and the panel; Browsr's existing panel code becomes its Browsr page; the project list mod moves into this repo as `spacr`. Switching Spacr swaps the block in the shown layout between `spacr` and Herdr's own `spaces` via `layout.set`.

**Tech Stack:** Python 3 (stdlib `unittest`), Herdr app mods (ES modules, `gpui-kit`, `gpui-base`, `herdr`), Node `node:test` for pure JS helpers.

**Spec:** `docs/superpowers/specs/2026-11-04-powr-panel-design.md`

## Global Constraints

- Shared key for the switch: `powr.modules`, value `{ browsr: boolean, spacr: boolean }`; a module missing from it is ON.
- Existing shared keys keep their names: `browsr.on`, `browsr.accent`, `browsr.panel`, `powr.folders`, `powr.space-icons`.
- Mod names: `powr` (button + panel), `spacr` (project list). `mods/browsr/` is deleted by the end.
- Panel popup: top right, `at: { x: 99999, y: 46 }`, width 720, height 480.
- Refusal text when Browsr is off, verbatim: `Browsr is switched off in the Powr panel.`
- Never write the app's `shared.json` from outside the app (the app overwrites it). Tests use a temp app home via `patch.object(bridge, "app_home", ...)`.
- Do not open overlays or change layouts in the person's running window during Tasks 1–5. Task 6 (install) is done by the lead, not a subagent.
- Run only the test files named in each task.
- Commit messages in English, ending with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_0162kQWxP6tu3PYLJ9iC6MFQ`

---

### Task 1: Bring the project list into the pack as `spacr`

**Files:**
- Merge: history of `~/Projects/powr` (branch `master` or whatever `git -C ~/Projects/powr branch --show-current` prints)
- Move: `mods/powr-spaces/` → `mods/spacr/`
- Modify: `mods/spacr/main.js`, `mods/spacr/mod.json`
- Delete: the merged-in top-level `README.md` / `LICENSE` from powr if they conflict (keep this repo's)

**Interfaces:**
- Produces: mod `spacr` at `mods/spacr/`, overlay roles `look` and `folder` opened with `mod: "spacr"`.

- [ ] **Step 1: Merge powr's history**

```bash
cd /home/g00rek/Projects/browsr
git remote add powr ~/Projects/powr
git fetch powr
git merge --allow-unrelated-histories --no-commit powr/$(git -C ~/Projects/powr branch --show-current)
git checkout --ours README.md LICENSE 2>/dev/null || true
git add README.md LICENSE 2>/dev/null || true
git mv mods/powr-spaces mods/spacr
git commit -m "merge: the project list joins the pack as spacr"
git remote remove powr
```

- [ ] **Step 2: Rename every reference inside the mod**

Every string `mods/powr-spaces/` becomes `mods/spacr/`, every `mod: "powr-spaces"` becomes `mod: "spacr"`, `ICON_INDEX` becomes `${appHome()}/mods/spacr/icons/index.json`, and `mod.json` title becomes `"Spacr"`.

```bash
sed -i 's#mods/powr-spaces/#mods/spacr/#g; s#mod: "powr-spaces"#mod: "spacr"#g' mods/spacr/main.js
sed -i 's#"Powr spaces"#"Spacr"#' mods/spacr/mod.json
grep -n "powr-spaces" mods/spacr/main.js mods/spacr/mod.json
```

Expected: the grep prints nothing. CSS-style ids like `powr-list`, `powr-tile-…` are element ids, not paths; leave them.

- [ ] **Step 3: Check it loads**

Run: `node --check mods/spacr/main.js && herdr-app check mods/spacr`
Expected: `check passed`.

- [ ] **Step 4: Commit**

```bash
git add -A mods/spacr
git commit -m "refactor: powr-spaces is spacr"
```

---

### Task 2: `bridge.py` knows when Browsr is switched off

**Files:**
- Modify: `bridge.py` (near `app_shared()` ~line 338; `start_watcher` ~850; `sync_and_follow` ~872; `restart_browser` ~909; `launch` ~934; `ask_extension` ~1525; command table ~1650)
- Test: `tests/test_bridge.py`

**Interfaces:**
- Produces: `bridge.MODULES_KEY = "powr.modules"`, `bridge.BROWSR_OFF = "Browsr is switched off in the Powr panel."`, `bridge.browsr_switched_on() -> bool`, command `bridge.py --app off` (quits the browser and stops every watcher).

- [ ] **Step 1: Write the failing tests** (add to the `BridgeTests` class in `tests/test_bridge.py`, next to the other `browsr.on` tests)

```python
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
```

- [ ] **Step 2: Run them, see them fail**

Run: `python3 -m unittest tests.test_bridge -k switched -k browsr_is_on -k off_quits -v`
Expected: FAIL / ERROR with `AttributeError: module 'bridge' has no attribute 'browsr_switched_on'`.

- [ ] **Step 3: Implement**

After `def app_shared():` add:

```python
# Which of the pack's modules are switched on, set in the Powr panel. A
# module not named in it is on: the pack works fully before anyone opens
# the panel.
MODULES_KEY = "powr.modules"
BROWSR_OFF = "Browsr is switched off in the Powr panel."


def browsr_switched_on():
    modules = app_shared().get(MODULES_KEY)
    if not isinstance(modules, dict):
        return True
    return modules.get("browsr") is not False


def refuse_when_switched_off():
    if not browsr_switched_on():
        raise RuntimeError(BROWSR_OFF)
```

Add `refuse_when_switched_off()` as the first line of `start_watcher()` (before the `herdr_host()` check), `sync_and_follow()`, `launch()` and `restart_browser()`; and in `ask_extension`, right after the "No workspace" check and before `if kind in ("open_url", "open_tab")`:

```python
    refuse_when_switched_off()
```

Add the off command next to `restart_browser`:

```python
def switch_off():
    """What the Powr panel runs when Browsr is switched off: nothing of it
    keeps running, and nothing starts it again until it is switched on."""
    stop_watchers()
    quit_browser()
```

Register it: add `"off"` to the usage string and the set of valid commands, and `"off": switch_off,` to the command table.

- [ ] **Step 4: Run the bridge tests**

Run: `python3 -m unittest tests.test_bridge`
Expected: `OK` (all tests, the four new ones included).

- [ ] **Step 5: Commit**

```bash
git add bridge.py tests/test_bridge.py
git commit -m "feat: bridge.py refuses to run Browsr while it is switched off in Powr"
```

---

### Task 3: Swapping the project list in a layout tree

**Files:**
- Create: `mods/powr/layout-swap.js`
- Test: `tests/layout-swap.test.mjs`

**Interfaces:**
- Produces: `export function swapMod(tree, from, to)` → `{ tree, changed }` — a deep copy of `tree` where every block with `content.mod === from` has `content.mod = to` (props kept); `changed` is the number of blocks changed.

- [ ] **Step 1: Write the failing test**

```js
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { swapMod } from '../mods/powr/layout-swap.js';

const tree = {
	type: 'column', children: [
		{ id: 'spaces', type: 'block', content: { mod: 'spacr' }, size: { weight: 1 } },
		{ id: 'agents', type: 'block', content: { mod: 'agents' } },
		{ id: 'row', type: 'row', children: [
			{ id: 'b', type: 'block', content: { mod: 'powr', props: { role: 'button' } } },
		] },
	],
};

test('the list block changes mod, nothing else does', () => {
	const { tree: out, changed } = swapMod(tree, 'spacr', 'spaces');
	assert.equal(changed, 1);
	assert.equal(out.children[0].content.mod, 'spaces');
	assert.deepEqual(out.children[0].size, { weight: 1 });
	assert.equal(out.children[1].content.mod, 'agents');
	assert.deepEqual(out.children[2].children[0].content, { mod: 'powr', props: { role: 'button' } });
});

test('the original tree is left as it was', () => {
	swapMod(tree, 'spacr', 'spaces');
	assert.equal(tree.children[0].content.mod, 'spacr');
});

test('a layout without the list reports no change', () => {
	const { changed } = swapMod({ type: 'block', content: { tab: 'active' } }, 'spacr', 'spaces');
	assert.equal(changed, 0);
});
```

- [ ] **Step 2: Run it, see it fail**

Run: `node --test tests/layout-swap.test.mjs`
Expected: FAIL, cannot find module `../mods/powr/layout-swap.js`.

- [ ] **Step 3: Implement**

```js
// Swapping which mod a layout block shows, as a pure function so it can be
// tested outside Herdr. Used to put Herdr's own project list back when Spacr
// is switched off, and Spacr back when it is switched on.

export function swapMod(tree, from, to) {
	let changed = 0;
	const walk = (node) => {
		if (Array.isArray(node)) return node.map(walk);
		if (!node || typeof node !== 'object') return node;
		const copy = {};
		for (const [key, value] of Object.entries(node)) copy[key] = walk(value);
		if (copy.type === 'block' && copy.content && copy.content.mod === from) {
			copy.content = { ...copy.content, mod: to };
			changed += 1;
		}
		return copy;
	};
	return { tree: walk(tree), changed };
}
```

- [ ] **Step 4: Run it**

Run: `node --test tests/layout-swap.test.mjs`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add mods/powr/layout-swap.js tests/layout-swap.test.mjs
git commit -m "feat: swap the mod a layout block shows"
```

---

### Task 4: The `powr` mod — button, panel, module switches, pages

**Files:**
- Create: `mods/powr/main.js`, `mods/powr/mod.json`, `mods/powr/powr.svg`
- Copy: `mods/browsr/check.svg` → not needed; `mods/browsr/browsr.svg` → `mods/powr/browsr.svg` (used on the Browsr page heading)
- Read first: `mods/browsr/main.js` (its code moves here), `~/.config/herdr-app/AGENTS.md` (mod API)

**Interfaces:**
- Consumes: `swapMod` from `./layout-swap.js`; `bridge.py --app status|launch|restart|sync|off`; shared `powr.modules`, `browsr.panel` (renamed in this mod to `powr.panel`), `browsr.on`, `browsr.accent`.
- Produces: mod `powr` with props role `button` (the square in the chrome) and anything else (the panel). Shared value `powr.modules`.

- [ ] **Step 1: `mods/powr/mod.json`**

```json
{
  "place": "none",
  "title": "Powr",
  "overlay": { "kind": "popup", "at": { "x": 99999, "y": 46 }, "width": 720, "height": 480 }
}
```

- [ ] **Step 2: `mods/powr/powr.svg`** — the ram's head, one colour, `currentColor`, 24×24 viewBox, readable at 16px: a rounded head in profile facing left, one spiral horn, one round eye. Stroke-based like the Lucide icons (`stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"`):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M4 15c0-4 2.5-7 6.5-7.5"/>
  <path d="M4 15c0 1.6 1 2.5 2.4 2.5h3.1L12 21"/>
  <path d="M10.5 7.5A5 5 0 1 1 15 16.5a2.5 2.5 0 1 1 0-5"/>
  <circle cx="8" cy="12" r="0.6" fill="currentColor"/>
</svg>
```

- [ ] **Step 3: `mods/powr/main.js`**

Structure (write it in full; the methods marked MOVED are copied verbatim from `mods/browsr/main.js` with only the constant names noted):

```js
import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { exec, spaces, focused, theme, home, notify, run, props, shared, setShared,
	closeOverlay, layout } from "herdr";
import { swapMod } from "./layout-swap.js";

const BRIDGE = `${home()}/Projects/browsr/bridge.py`;
const MODULES = "powr.modules";
const PANEL = "powr.panel";
const SAME_PRESS = 250;
const STATUS_EVERY = 4000;

// The modules this panel switches, in the order the column lists them.
const ALL = [
	{ id: "browsr", name: "Browsr", about: "A browser whose tab groups follow the project in front." },
	{ id: "spacr", name: "Spacr", about: "The project list: folders, branches, icons, colours, the browser mark." },
];

// MOVED from mods/browsr/main.js, unchanged: CHROME_COLORS, ACCENT ("browsr.accent"),
// RESTING, rgb(), nearestColor().

export default class Powr extends View {
	init(_props, cx) {
		this.status = null;
		this.note = "";
		this.working = null;
		this.page = "browsr";
		if (!this.isButton()) {
			setShared(PANEL, { open: true, changed: Date.now() });
			cx.timer.every(STATUS_EVERY, (cx) => this.refresh(cx));
			this.refresh(cx);
		}
	}

	async close() {
		if (!this.isButton()) setShared(PANEL, { open: false, changed: Date.now() });
	}

	isButton() { return props()?.role === "button"; }

	/** { browsr: bool, spacr: bool }, every module on unless set off. */
	modules() {
		const stored = shared(MODULES);
		const set = stored && typeof stored === "object" ? stored : {};
		return Object.fromEntries(ALL.map((m) => [m.id, set[m.id] !== false]));
	}

	/**
	 * Switch a module. The switch is stored first, so everything reading it
	 * agrees at once; then whatever the module runs is told.
	 */
	switchModule(id, on, cx) {
		setShared(MODULES, { ...this.modules(), [id]: on });
		if (id === "browsr" && !on) this.act(cx, "off", "switching off");
		if (id === "spacr") {
			const { tree, changed } = on
				? swapMod(layout(), "spaces", "spacr")
				: swapMod(layout(), "spacr", "spaces");
			if (changed) run("layout.set", { layout: tree });
		}
		if (!on && this.page === id) this.page = null;
		if (on) this.page = id;
		cx.notify();
	}

	// MOVED from mods/browsr/main.js, unchanged: pressedWhileOpen() (uses PANEL),
	// publishAccent(), refresh(cx), act(cx, argument, label), state(), words(), colour(t).

	renderButton() {
		// MOVED from mods/browsr/main.js renderButton(), with three changes:
		// id "powr-button", tooltip "Powr", run("mod.open", { name: "powr" }),
		// and the glyph is svg("mods/powr/powr.svg").size(16).text_color(t.foreground)
		// (no status tint: the button is the pack's, not Browsr's).
	}

	/** A switch drawn by hand: gpui-base's Switch has no styling of its own. */
	toggle(t, id, on, handler) {
		return div().id(`powr-switch-${id}`)
			.w(28).h(16).rounded(8).flex_none()
			.cursor_pointer()
			.bg(on ? t.accent : t.border)
			.on_click(handler)
			.child(div().size(12).rounded(6).mt(2).ml(on ? 14 : 2).bg(t.background));
	}

	renderColumn(t) {
		const on = this.modules();
		const column = v_flex().w(180).flex_none().h_full().p(8).gap(2)
			.border_r_1().border_color(t.border)
			.child(div().px(6).pb(4).text_size(11).text_color(t.dim).child("Modules"));
		for (const m of ALL) {
			column.child(h_flex().px(6).py(4).items_center().gap(8)
				.child(div().flex_1().text_color(on[m.id] ? t.foreground : t.dim).child(m.name))
				.child(this.toggle(t, m.id, on[m.id],
					(_event, cx) => this.switchModule(m.id, !on[m.id], cx))));
		}
		column.child(div().h(1).my(6).bg(t.border));
		for (const m of ALL.filter((m) => on[m.id])) {
			const picked = this.page === m.id;
			column.child(div().id(`powr-page-${m.id}`)
				.px(6).py(5).rounded(6).cursor_pointer()
				.bg(picked ? t.selected : t.popover)
				.text_color(picked ? t.foreground : t.dim)
				.hover((s) => s.bg(t.hover))
				.on_click((_event, cx) => { this.page = m.id; cx.notify(); })
				.child(m.name));
		}
		return column;
	}

	renderBrowsrPage(t) {
		const { words, fix } = this.state();
		const action = (label, argument, hint) =>
			div().id(`powr-${argument}`).px(9).py(3).rounded(5)
				.bg(t.surface)
				.text_color(this.working === label ? t.dim : t.foreground)
				.hover((s) => s.bg(t.hover))
				.tooltip(hint)
				.on_click((_event, cx) => this.act(cx, argument, label))
				.child(this.working === label ? "…" : label);
		return v_flex().gap(10)
			.child(h_flex().items_center().gap(8)
				.child(div().size(8).rounded(4).bg(this.colour(t)))
				.child(div().text_color(t.foreground).child("Browsr"))
				.child(div().flex_1().text_color(t.dim).child(words)))
			.child(h_flex().gap(6)
				.child(action("show browser", "launch",
					"Bring the browser window forward. The only thing that starts it."))
				.child(action("restart", "restart",
					"Close the browser and start it again, with everything that runs beside it."))
				.when(fix !== null, (el) => el.child(action(fix, "sync",
					"Make the groups match the projects that have a browser."))))
			.child(div().text_size(11).text_color(t.dim)
				.child("Which projects have a browser is set on the project list: the browser mark on each row."));
	}

	renderSpacrPage(t) {
		const line = (text) => div().text_size(12).text_color(t.dim).child(text);
		return v_flex().gap(8)
			.child(div().text_color(t.foreground).child("Spacr"))
			.child(line("Left press: select a project, fold or unfold a folder."))
			.child(line("Right press on a project's icon: its colour and glyph."))
			.child(line("Right press on the rest of a project's row: Herdr's own menu."))
			.child(line("Right press on a folder: its name, colour, glyph and Delete."))
			.child(line("Drag a project onto a folder to put it there, below the list to take it out."));
	}

	render() {
		if (this.isButton()) return this.renderButton();
		const t = theme();
		this.publishAccent();
		const on = this.modules();
		const page = this.page && on[this.page] ? this.page : null;
		const body = v_flex().flex_1().min_w(0).h_full().p(16).gap(10)
			.child(page === "browsr" ? this.renderBrowsrPage(t)
				: page === "spacr" ? this.renderSpacrPage(t)
				: div().text_color(t.dim).child("Switch a module on to see its page."));
		return h_flex()
			.id("root")
			.size_full()
			.bg(t.popover)
			.text_size(13)
			.on_mouse_down_out(() => {
				const overlay = props()?.overlay;
				if (overlay) closeOverlay(overlay);
			})
			.child(this.renderColumn(t))
			.child(body);
	}
}
```

`state()` (MOVED) reads `this.status.workspaces`, `this.status.strip`, `this.browsers()` — move `browsers()` too (it reads shared `browsr.on`). Do NOT move `seedBrowsers()`, `toggle(cx, space)`, `allOn`, `offCount` or the project list rendering: the list lives in Spacr.

- [ ] **Step 4: Check it**

Run: `node --check mods/powr/main.js && herdr-app check mods/powr && node --test tests/layout-swap.test.mjs`
Expected: syntax OK, `check passed`, 3 pass.

- [ ] **Step 5: Commit**

```bash
git add mods/powr
git commit -m "feat: the Powr button and panel, with Browsr and Spacr as modules"
```

---

### Task 5: Spacr steps aside for Browsr when Browsr is off

**Files:**
- Modify: `mods/spacr/main.js` (the browser mark in `row(...)`, the `N without a browser` line in `render()`, `pickUpQuestion`)

**Interfaces:**
- Consumes: shared `powr.modules` (Task 4).

- [ ] **Step 1: Implement**

Add near the other shared-key constants:

```js
// The pack's module switches, set in the Powr panel. Browsr is on unless it
// says otherwise.
const MODULES = "powr.modules";
```

Add a method on the class:

```js
	/** Whether Browsr is switched on in the Powr panel. */
	browsrOn() {
		const modules = shared(MODULES);
		return !(modules && typeof modules === "object" && modules.browsr === false);
	}
```

Then:
- in `row(...)`, wrap the browser-mark child (the element with `.tooltip(on ? "Has a browser. …" : …)`) in `.when(this.browsrOn(), (el) => el.child(…))` so the mark is not drawn while Browsr is off;
- in `render()`, change `.when(off > 0, …)` to `.when(off > 0 && this.browsrOn(), …)`;
- at the top of `pickUpQuestion(cx)`, `if (!this.browsrOn()) return;`.

- [ ] **Step 2: Check it**

Run: `node --check mods/spacr/main.js && herdr-app check mods/spacr`
Expected: `check passed`.

- [ ] **Step 3: Commit**

```bash
git add mods/spacr/main.js
git commit -m "feat: spacr hides the browser mark while Browsr is switched off"
```

---

### Task 6 (lead only): retire `mods/browsr`, install, verify in the real window

- [ ] **Step 1:** `git rm -r mods/browsr`; update `scripts/check.sh` to run `node --test tests/*.test.mjs` (already globbed) and `node --check mods/powr/main.js mods/spacr/main.js`.
- [ ] **Step 2:** Back up `~/.config/herdr-app/layouts/main-folders.json` and `~/.config/herdr-app/mods/{browsr,powr-spaces}` to `~/.local/state/claude-backups/powr-install-<date>/`.
- [ ] **Step 3:** Copy `mods/powr` and `mods/spacr` into `~/.config/herdr-app/mods/`; in the shown layout, swap `browsr` → `powr` (button block) and `powr-spaces` → `spacr` with `layout.set`; then remove the installed `browsr` and `powr-spaces` folders. `herdr-app status` shows no errors.
- [ ] **Step 4:** In the window: Powr button opens/closes the panel; Browsr off → browser gone, marks gone from Spacr; Browsr on → `show browser` brings it back; Spacr off → Herdr's list; Spacr on → folders, icons, colours unchanged. One screenshot per state, no repeated overlay opening.
- [ ] **Step 5:** Run `scripts/check.sh`; commit `chore: Browsr's own mod gives way to Powr`.
