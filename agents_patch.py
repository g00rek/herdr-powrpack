"""A local copy of Herdr's Agents list with a Switchr badge and menu.

Herdr's list is its own mod and cannot be decorated from outside, so the
installer clones it (`switchr-agents`) and patches three places. Herdr's code
is never stored in this repo: only these anchors and the small addon.
"""
import json
import re
import shutil
import time
from pathlib import Path

ADDON = Path(__file__).resolve().parent / "mods" / "switchr-agents-addon" / "switchr.js"
COPY = "switchr-agents"

IMPORT = 'import { switchrBadge, switchrMenu } from "./switchr.js";'
MACHINE = '.when(Boolean(machine), el => el.child(div().text_size(10).text_color(t.dim).child(machine)))'
BADGE = '.when(Boolean(switchrBadge(a)), el => el.child(div().text_size(10).text_color(t.dim).child(switchrBadge(a))))'
CLICK = '.on_click(() => run("pane.focus", { pane: a.pane }))'
MENU = '.on_mouse_down("right", (_e, cx) => { cx.stop_propagation(); switchrMenu(a); })'
HOVER_CLICK = re.compile(r"(\.on_hover\((?:(?!\.on_hover\(|\.on_click\().)*?\)\s*)(" + re.escape(CLICK) + ")", re.S)


class PatchError(Exception):
    pass


def _once(source, anchor, name):
    found = source.count(anchor)
    if found != 1:
        raise PatchError(f"anchor '{name}' is {'missing' if not found else f'present {found} times'}")


def _last_import_end(lines):
    last = None
    for i, line in enumerate(lines):
        if re.match(r"import\b", line):
            last = i
    if last is None:
        raise PatchError("anchor 'import line' is missing")
    while not re.search(r"""["'];?\s*$""", lines[last]) and last + 1 < len(lines):
        last += 1
    return last


def patch(source):
    if IMPORT in source:
        return source
    _once(source, MACHINE, "machine label")
    # The row's click is the one right after its hover; a smaller row above has a click too.
    row = list(HOVER_CLICK.finditer(source))
    if len(row) != 1:
        raise PatchError(f"anchor 'row hover then click' is {'missing' if not row else f'present {len(row)} times'}")
    at = row[0].start(2)
    out = source[:at] + MENU + "\n        " + source[at:]
    out = out.replace(MACHINE, MACHINE + "\n        " + BADGE)
    lines = out.split("\n")
    lines.insert(_last_import_end(lines) + 1, IMPORT)
    return "\n".join(lines)


POLL = 0.2
WAIT = 10.0


def _sizes(target):
    try:
        return tuple((target / name).stat().st_size for name in ("main.js", "mod.json"))
    except OSError:
        return None


def _wait_for_copy(target):
    """True once main.js and mod.json exist and have stopped changing size."""
    deadline = time.monotonic() + WAIT
    last = None
    while time.monotonic() < deadline:
        now = _sizes(target)
        if now is not None and now == last:
            return True
        last = now
        time.sleep(POLL)
    return False


def _build(target, herdr_app):
    cloned = herdr_app("do", "mod.clone", json.dumps({"name": "agents", "as": COPY}))
    if cloned.returncode:
        return f"Badge not added: Herdr would not copy its Agents list ({(cloned.stderr or cloned.stdout).strip()})."
    if not _wait_for_copy(target):
        return "Badge not added: the copy of Herdr's Agents list did not appear."
    main = target / "main.js"
    try:
        main.write_text(patch(main.read_text()))
    except PatchError as error:
        return f"Badge not available with this Herdr version ({error})."
    shutil.copy2(ADDON, target / "switchr.js")
    check = herdr_app("check", str(target))
    if check.returncode:
        return f"Badge not added: the patched Agents list did not load ({(check.stdout + check.stderr).strip()})."
    return None


def make_copy(home, herdr_app):
    """Clone Herdr's agents list as switchr-agents and patch it.

    Returns (ok, one plain sentence). On ok=False the previous working copy, if
    there was one, is exactly as it was; no partial folder is left behind. The
    old copy waits in <home>/switchr-agents.prev (outside mods/) meanwhile.
    """
    home = Path(home)
    target = home / "mods" / COPY
    aside = home / (COPY + ".prev")
    shutil.rmtree(aside, ignore_errors=True)
    had_old = target.exists()
    if had_old:
        shutil.move(str(target), str(aside))
    try:
        failure = _build(target, herdr_app)
    except Exception as error:  # herdr-app missing, unreadable or unwritable files, ...
        failure = f"Badge not added: {error}."
    if failure is None:
        shutil.rmtree(aside, ignore_errors=True)
        return True, "Agents list with account badge installed."
    shutil.rmtree(target, ignore_errors=True)
    if had_old:
        shutil.move(str(aside), str(target))
    return False, failure
