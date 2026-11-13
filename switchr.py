#!/usr/bin/env python3
"""Switchr: run each Claude Code / Codex terminal on the account you choose.

The main account is the agent's own folder (~/.claude with ~/.claude.json,
~/.codex) and is never written here. An extra account lives in a folder of its
own under the data directory whose entries are symlinks to the main folder, so
settings, skills, history and projects are shared, except for the login.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- constants

KINDS = ("claude", "codex")
ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
AGENT = {"claude": "claude", "codex": "codex"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")

# Entries of the main folder that must NOT be shared: each extra home keeps its
# own. Everything else is linked. Settled 2026-11-13 by listing both folders
# on a working machine and asking of each entry: would two agents on different
# accounts, running at once, corrupt it or leak one account into the other?
OWN = {
    "claude": {
        ".credentials.json",  # the login itself
        ".claude.json",  # account identity and per-account caches; main's lives outside ~/.claude
        ".claude.json.lock",  # lock for that file
        "backups",  # backups of .claude.json
        "daemon",  # background process state, one set per running config
        "policy-limits.json",  # organisation policy: differs per account
        "policy-limits.json.signature-iat.json",
        "policy-limits.json.signature.json",
        "policy-limits.json.stamp.json",
        "remote-settings.json",  # organisation-managed settings: differs per account
        "remote-settings.json.signature-iat.json",
        "remote-settings.json.signature.json",
        "remote-settings.json.synced-at",
    },
    "codex": {
        "auth.json",  # the login itself
        "auth.json.lock",  # lock for that file
        "auth.lock",
        "accounts",  # saved logins
        ".current-account",  # which login is active
        "app-server-control",  # live control socket of one running process
        "app-server-daemon",  # background process state
        "ipc",  # sockets between running processes
        "node_repl",  # per-process helper state
        "models_cache.json",  # model list depends on the plan of the account
    },
}

# Keys of main ~/.claude.json copied into an extra account's own .claude.json,
# so MCP servers, project trust and onboarding carry over without the login.
SHARED_CLAUDE_KEYS = ("mcpServers", "projects", "githubRepoPaths",
                      "hasCompletedOnboarding", "lastOnboardingVersion")


# -------------------------------------------------------------------- paths

def data_dir():
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "switchr"


def main_home(kind):
    return Path.home() / (".claude" if kind == "claude" else ".codex")


def main_claude_json():
    return Path.home() / ".claude.json"


# ----------------------------------------------------------------- registry

def _empty_registry():
    return {"main": {"claude": "main", "codex": "main"}, "accounts": []}


def load_registry():
    try:
        reg = json.loads((data_dir() / "accounts.json").read_text())
    except (OSError, ValueError):
        return _empty_registry()
    if not isinstance(reg, dict):
        return _empty_registry()
    base = _empty_registry()
    base.update({k: v for k, v in reg.items() if k in base})
    return base


def save_registry(reg):
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".accounts-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(reg, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, directory / "accounts.json")
    except BaseException:
        with_suppress_unlink(tmp)
        raise


def with_suppress_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def account(kind, name):
    reg = load_registry()
    if name == reg["main"].get(kind, "main"):
        return {"kind": kind, "name": name, "home": None, "main": True}
    for entry in reg["accounts"]:
        if entry.get("kind") == kind and entry.get("name") == name:
            return entry
    return None


# ------------------------------------------------------------------- homes

def _sync_claude_json(home):
    try:
        main = json.loads(main_claude_json().read_text())
    except (OSError, ValueError):
        return
    if not isinstance(main, dict):
        return
    target = home / ".claude.json"
    try:
        own = json.loads(target.read_text())
        if not isinstance(own, dict):
            own = {}
    except (OSError, ValueError):
        own = {}
    for key in SHARED_CLAUDE_KEYS:
        if key in main:
            own[key] = main[key]
    fd, tmp = tempfile.mkstemp(dir=home, prefix=".claude.json-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(own, handle, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        with_suppress_unlink(tmp)
        raise


def prepare_home(kind, home):
    """Make `home` an extra account folder that mirrors main except the login."""
    home = Path(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    main = main_home(kind)
    for link in home.iterdir():
        if link.is_symlink() and not link.exists():
            link.unlink()
    if main.is_dir():
        for entry in main.iterdir():
            if entry.name in OWN[kind]:
                continue
            link = home / entry.name
            if link.is_symlink():
                if Path(os.readlink(link)) == entry:
                    continue
                link.unlink()
            elif link.exists():
                continue  # a real file here is the account's own; leave it
            link.symlink_to(entry)
    if kind == "claude":
        _sync_claude_json(home)


# ---------------------------------------------------------------- launcher

def launch_argv(kind, name, args):
    """Environment changes and argv for an agent on `name`.

    The environment is a dict of changes; a None value means "remove".
    """
    entry = account(kind, name)
    if entry is None:
        raise SystemExit(f"switchr: no {kind} account named {name!r}")
    argv = [AGENT[kind], *args]
    if entry.get("main"):
        return {ENV[kind]: None}, argv
    return {ENV[kind]: entry["home"]}, argv


def run(kind, name, args):
    env_changes, argv = launch_argv(kind, name, args)
    entry = account(kind, name)
    if not entry.get("main"):
        prepare_home(kind, Path(entry["home"]))
    env = dict(os.environ)
    for key, value in env_changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    try:
        os.execvpe(argv[0], argv, env)
    except OSError as error:
        raise SystemExit(f"switchr: cannot start {argv[0]}: {error}")


# --------------------------------------------------------------------- CLI

def build_parser():
    parser = argparse.ArgumentParser(prog="switchr")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="start an agent on an account")
    run_cmd.add_argument("kind", choices=KINDS)
    run_cmd.add_argument("name")
    run_cmd.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv=None):
    options = build_parser().parse_args(argv)
    if options.command == "run":
        args = options.args
        if args and args[0] == "--":
            args = args[1:]
        run(options.kind, options.name, args)


if __name__ == "__main__":
    sys.exit(main())
