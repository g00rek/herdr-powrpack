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


def make_copy(home, herdr_app):
    """Clone Herdr's agents list as switchr-agents and patch it. One-line result."""
    target = Path(home) / "mods" / COPY
    shutil.rmtree(target, ignore_errors=True)
    cloned = herdr_app("do", "mod.clone", json.dumps({"name": "agents", "as": COPY}))
    if cloned.returncode:
        return f"Badge not added: Herdr would not copy its Agents list ({(cloned.stderr or cloned.stdout).strip()})."
    for _ in range(50):
        if (target / "main.js").exists():
            break
        time.sleep(0.2)
    else:
        return "Badge not added: the copy of Herdr's Agents list did not appear."
    main = target / "main.js"
    try:
        main.write_text(patch(main.read_text()))
    except PatchError as error:
        shutil.rmtree(target, ignore_errors=True)
        return f"Badge not available with this Herdr version ({error})."
    shutil.copy2(ADDON, target / "switchr.js")
    check = herdr_app("check", str(target))
    if check.returncode:
        shutil.rmtree(target, ignore_errors=True)
        return f"Badge not added: the patched Agents list did not load ({(check.stdout + check.stderr).strip()})."
    return "Agents list with account badge installed."
