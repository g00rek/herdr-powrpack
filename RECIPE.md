# Herdr Powr Pack

Two mods for the Herdr desktop app and the browser helper behind one of them.
A **Powr Pack** button under Settings opens one control panel; there, Browsr
(a tab group per space in a dedicated Chromium) and Spacr (the project list
with folders, colours, icons and worktrees under their project) are switched
on and off and set up.

![Herdr Powr Pack](preview.png)

## What you see

- **Sidebar footer:** a 36px row under Herdr's own footer, lightning icon and
  "Powr Pack" in the footer's style. Below 160px of sidebar only the icon.
- **Powr panel:** a centered dialog, 880×621.5 logical px, the size and place of
  Herdr's Settings. Left column: every module with icon and name, switched-off
  ones at half opacity. Right: the selected module's icon, name, one-sentence
  description and an on/off switch; under a rule, its buttons stacked, each
  110px wide with a short note beside it, then one status line, then a
  SETTINGS section of rows (name, note, control on the right).
- **Spacr list** (in place of Herdr's `spaces` block): header "spaces" with a
  new-folder and a new-space button. Folders first, each a 22px tile tinted
  with its colour at 25% under its icon, then name, project count and a
  chevron; its projects hang off the tile by 1px lines in the folder colour.
  A project is a filled 22px tile in its colour with a white icon or letter,
  its name, the branch under it when it differs, the agent status and the
  browser mark. Worktrees of the same repository hang off their project by
  lines in the project's colour, with an unframed 13px branch icon.

## How it behaves

- Browsr page: **Show browser** raises the browser (starting it), **Restart**
  restarts it and reloads the extension, **Resync** appears only on a fault.
  Status lines end in "Click X to fix it." Settings: New spaces (switch),
  Agent asks (Ask / Turn on / Refuse), Fold other groups (switch, on).
- Spacr page: how to use the list, and Folder background (None / Light /
  Medium / Strong).
- Switching Spacr off swaps the layout block back to Herdr's `spaces`.
- In the list: click selects a space; right-click a tile for colour and icon;
  right-click a row for Herdr's own menu; right-click a folder for its name,
  colour and icon; drag a project onto a folder, or below the list to take it
  out; click a folder to fold it; the browser mark toggles Browsr for that
  space. A new worktree is hidden until its repository is known (2s at most),
  so it never appears loose first.

## Built from

- `mods/powr` (`place: none`, overlay `dialog`), roles: `button` in the
  layout, the panel as its overlay. `mods/spacr` (`place: none`) in the
  sidebar's `spaces` block.
- `herdr` API: `shared`/`setShared` (keys `powr.*`, `browsr.*`, `spacr.wash`),
  `spaces`, `panes`, `layout`, `run("layout.set")`, `openOverlay`, `exec`.
- Programs: `bridge.py` (sync, launch, restart, status, off) and `git`
  (to find which spaces share a repository).
- Art: Lucide icons in `mods/spacr/icons`, Herdr's branch icon.
- `install.py` copies the mods, writes the checkout's path into each
  `main.js` (`const BRIDGE`), merges `keys.json`, clones a shipped layout and
  adds the button block.

## Watch out

- Mods run commands (`exec`): `bridge.py` and `git`. Browsr starts a Chromium
  with DevTools on 127.0.0.1:39222.
- A render that does too much is interrupted by Herdr; Spacr works out each
  row's project and folder once per render.
- Never write the app's `shared.json` from outside the app.

## Done when

- `herdr-app check` passes for both mods.
- The Powr Pack button opens the panel at Settings' size and place.
- Switching spaces brings that space's tab group forward in the browser.
- A worktree appears under its project, never loose first.
