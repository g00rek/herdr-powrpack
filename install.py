#!/usr/bin/env python3
"""Install the Herdr Powr Pack into the Herdr desktop app.

Run from the checkout, as often as you like:

    ./install.py

It does four things, and each is safe to repeat:

1. Sets up the browser half: finds Chromium and registers the helper that
   lets Browsr's extension talk to Herdr (``bridge.py setup``).
2. Copies the ``powr`` and ``spacr`` mods into the app's home, pointed at
   this checkout so they can call ``bridge.py`` wherever it was cloned.
3. Adds ``alt-b`` (open the Powr panel) to ``keys.json``, unless that key
   already does something else.
4. Puts the Powr Pack button in the sidebar and Spacr in place of Herdr's
   project list. A shipped layout is read-only,
   so it is cloned first (``<layout>-powr``) and the clone is switched to;
   a layout of your own is edited in place, after a backup.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("HERDR_APP_HOME", Path.home() / ".config" / "herdr-app"))
MODS = ("powr", "spacr")
# Written into a mod folder by the app itself; not ours to ship or copy.
SKIP = {"gpui-kit.d.ts", "herdr.d.ts", "jsconfig.json"}
KEY, OPEN = "alt-b", "mod.open:powr"
BUTTON = {
    "id": "powr-footer",
    "type": "block",
    "content": {"mod": "powr", "props": {"role": "button"}},
    "size": {"px": 36},
}


def say(line):
    print(f"  {line}")


def herdr_app(*args):
    return subprocess.run(["herdr-app", *args], capture_output=True, text=True)


def setup_browser():
    result = subprocess.run([sys.executable, str(ROOT / "bridge.py"), "setup"],
                            capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"Browser setup failed:\n{result.stderr.strip()}")
    say("Browser set up.")


def copy_mods():
    bridge = json.dumps(str(ROOT / "bridge.py"))
    for name in MODS:
        source, target = ROOT / "mods" / name, HOME / "mods" / name
        target.mkdir(parents=True, exist_ok=True)
        for path in source.rglob("*"):
            if path.name in SKIP or path.is_dir():
                continue
            out = target / path.relative_to(source)
            out.parent.mkdir(parents=True, exist_ok=True)
            if path.name == "main.js":
                text = re.sub(r"^const BRIDGE = .*;$", f"const BRIDGE = {bridge};",
                              path.read_text(), count=1, flags=re.M)
                out.write_text(text)
            else:
                shutil.copy2(path, out)
        check = herdr_app("check", str(target))
        if check.returncode:
            sys.exit(f"The {name} mod did not load:\n{check.stdout}{check.stderr}")
    say("Mods installed: " + ", ".join(MODS) + ".")


def add_key():
    path = HOME / "keys.json"
    keys = json.loads(path.read_text()) if path.exists() else {}
    used = keys.setdefault("global", {})
    if used.get(KEY) == OPEN:
        return say(f"{KEY} already opens the Powr panel.")
    if KEY in used:
        return say(f"{KEY} is taken ({used[KEY]}); add \"<key>\": \"{OPEN}\" to keys.json yourself.")
    if path.exists():
        shutil.copy2(path, path.with_name(f"keys.json.before-powr-{int(time.time())}"))
    used[KEY] = OPEN
    path.write_text(json.dumps(keys, indent=2) + "\n")
    say(f"{KEY} opens the Powr panel.")


def find_footer(node, parent=None):
    """The list that holds Herdr's sidebar footer, and where it is in it."""
    if isinstance(node, dict):
        content = node.get("content") or {}
        if content.get("mod") == "workspace-chrome" and (content.get("props") or {}).get("footer"):
            return parent, node
        for child in node.get("children", []):
            found = find_footer(child, node)
            if found:
                return found
    return None


def swap_list(node):
    """Spacr in place of Herdr's project list: the panel shows it on by default."""
    if isinstance(node, dict):
        content = node.get("content") or {}
        if node.get("type") == "block" and content.get("mod") == "spaces":
            content["mod"] = "spacr"
        for child in node.get("children", []):
            swap_list(child)


def uses(node, mod):
    return f'"mod": "{mod}"' in json.dumps(node)


def place_button():
    try:
        active = json.loads((HOME / "shared.json").read_text()).get("layout") or "main"
    except (OSError, ValueError):
        active = "main"
    path = HOME / "layouts" / f"{active}.json"
    if not path.exists():
        return say(f"Layout {active} not found; press {KEY} to open the Powr panel.")
    tree = json.loads(path.read_text())
    if uses(tree, "powr"):
        return say(f"Layout {active} already has the Powr Pack button.")

    if "cloned_from" not in tree and active in {"main", "terminals", "win98"}:
        # Herdr's own layouts are replaced whole on update; work on a clone.
        clone = f"{active}-powr"
        if not (HOME / "layouts" / f"{clone}.json").exists():
            result = herdr_app("do", "layout.clone", json.dumps({"name": active, "as": clone}))
            if result.returncode:
                return say(f"Could not clone layout {active}; press {KEY} to open the Powr panel.")
        else:
            herdr_app("do", "layout.switch", json.dumps({"name": clone}))
        path = HOME / "layouts" / f"{clone}.json"
        for _ in range(50):
            if path.exists():
                break
            time.sleep(0.1)
        tree = json.loads(path.read_text())
        if uses(tree, "powr"):
            return say(f"Switched to layout {clone}, which has the Powr Pack button.")
    else:
        shutil.copy2(path, path.with_name(f"{path.name}.before-powr-{int(time.time())}"))

    found = find_footer(tree)
    if not found:
        return say(f"No sidebar footer in layout {path.stem}; press {KEY} to open the Powr panel.")
    parent, footer = found
    children = parent["children"]
    children.insert(children.index(footer) + 1, dict(BUTTON))
    if footer.get("size", {}).get("px", 0) > 60:
        footer["size"] = {"px": 60}
    swap_list(tree)
    path.write_text(json.dumps(tree, indent=2) + "\n")
    say(f"Layout {path.stem}: Powr Pack button under Settings, Spacr as the project list.")


def main():
    if not shutil.which("herdr-app"):
        sys.exit("herdr-app is not on PATH. Install the Herdr desktop app first: https://herdr.dev")
    print("Installing the Herdr Powr Pack")
    setup_browser()
    copy_mods()
    add_key()
    place_button()
    print("Done. Open the panel with the Powr Pack button or alt-b, and switch modules on there.")


if __name__ == "__main__":
    main()
