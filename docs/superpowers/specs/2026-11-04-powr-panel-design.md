# Powr Pack: modules and one control panel

Decided with Michał, 2026-11-04. Phase 1 of the Herdr Powr Pack. The GitHub
repo (`herdr-powrpack`) comes after this, not before.

## What the person gets

- One button at the top right of Herdr's window — the Powr button, the ram
  from the pack's logo — replacing Browsr's own button in the same spot.
- Pressing it drops one large panel under it, flush right, about 720 x 480.
  A second press on the button, or a press anywhere beside the panel, puts it
  away (the same behaviour Browsr's panel has today).
- The panel is laid out like a settings window. A left column, about 180 wide:
  a **Modules** heading with one switch per module, and under it the name of
  every module that is switched on. The right side shows the page of the module
  picked in that column. One module's page at a time.
- Modules in phase 1: **Browsr** and **Spacr**. **Swapr** (an agent account
  per session) is phase 2 and gets its own design; it does not appear in the
  panel until it does something.

## Modules

| Module | What it is | Where it lives |
|---|---|---|
| Browsr | a browser whose tab groups follow the project in front | `bridge.py`, `extension/`, and a page in the Powr panel |
| Spacr | the project list: folders, branches under their project, icons, colours, the browser mark | mod `spacr` (today `powr-spaces` in `~/Projects/powr`) |
| Swapr | phase 2 | — |

The pack is installed once. Modules are switched on and off in the panel.

### The switch, and what it does

The state is one shared value, `powr.modules = { browsr: bool, spacr: bool }`,
read by every mod and by `bridge.py` (which already reads the app's shared
state for `browsr.on`). A module missing from it counts as on.

**Browsr off**
- The browser and the watcher stop (`quit` + stop every watcher).
- Nothing follows the project in front; nothing starts the browser:
  `launch`, `sync` and `start_watcher` refuse while it is off.
- An agent asking for a page gets a plain refusal ("Browsr is switched off in
  the Powr panel") instead of the on-screen question.
- Spacr hides its browser mark on every row, and its "N without a browser"
  count.
- On again: nothing is restarted automatically; "show browser" brings it back
  with its tabs, as it does today after a quit.

**Spacr off**
- The current personal layout gets Herdr's own `spaces` mod back in the block
  that shows `spacr`, through `layout.set`. Folders, icons and colours stay
  stored (`powr.folders`, `powr.space-icons`, Herdr's own colour map) and come
  back when it is switched on, which puts `spacr` back in that block.
- Turning a project's browser on and off lives only in Spacr. With Spacr off,
  the only way left is the question that appears when an agent wants a page.

## The pages

**Browsr**: the state line, `show browser`, `restart`, and `resync` only while
the state line names a fault — exactly today's Browsr panel without its
project list, which Spacr already shows on every row.

**Spacr**: no settings yet. One short line on how the list is used: left press
selects or folds, right press on a project's icon is its colour and glyph, on
the rest of its row Herdr's menu, on a folder its menu.

## Layout of the code

- Work happens in the browsr checkout, which is what becomes `herdr-powrpack`.
  `~/Projects/powr`'s history is merged in, with `mods/powr-spaces` moved to
  `mods/spacr`.
- `mods/powr/`: the button and the panel (roles `button` and `panel`). Browsr's
  page is today's Browsr panel code moved here.
- `mods/browsr/` is removed: its button and its panel are replaced by Powr's.
- Shared keys keep their names (`browsr.on`, `browsr.accent`, `powr.folders`,
  `powr.space-icons`), so nothing the person has set up is lost.
- The installed layout: the `browsr-button` block shows `powr` (role button),
  the `spaces` block shows `spacr`.

## Done when

- `bridge.py` tests: with `powr.modules.browsr = false`, `launch`, `sync` and
  `start_watcher` do nothing and say so, and an agent's page request is
  refused without writing a question.
- `herdr-app check` passes for `powr` and `spacr`; the app reports no errors.
- In the real window: the Powr button opens and closes the panel; switching
  Browsr off closes the browser and removes the marks from Spacr; switching
  Spacr off brings back Herdr's list, and on again restores folders, icons
  and colours unchanged.
