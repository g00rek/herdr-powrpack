#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

python3 -m unittest discover -s tests -v
node --test tests/*.test.mjs
python3 -m py_compile bridge.py native_host.py panel.py cdp_filter.py browsr-chrome-mcp.py install.py switchr.py agents_patch.py
for script in extension/service-worker.js mods/powr/main.js mods/powr/layout-swap.js mods/powr/switchr-accounts.js mods/spacr/main.js mods/switchr-agents-addon/switchr.js; do node --check "$script"; done
python3 -c 'import json, pathlib, tomllib; json.loads(pathlib.Path("extension/manifest.json").read_text()); tomllib.loads(pathlib.Path("herdr-plugin.toml").read_text())'
