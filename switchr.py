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
import base64
import contextlib
import fcntl
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
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


class SwitchrError(Exception):
    """A refusal the person should read; the CLI prints it and exits non-zero."""


# ------------------------------------------------------------------- homes

def _guard_home(kind, home):
    """Refuse any home that is a main folder, inside one, or outside the data directory."""
    path = Path(home).resolve()
    data = data_dir().resolve()
    for other in KINDS:
        main = main_home(other).resolve()
        if path == main or main in path.parents:
            raise SwitchrError(f"refusing {path}: it is (or is inside) the main {other} folder")
    if path == data or data not in path.parents:
        raise SwitchrError(f"refusing {path}: not inside {data}")
    return path


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
    _guard_home(kind, home)
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


# ---------------------------------------------------------------- accounts

def _jwt_payload(token):
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (AttributeError, IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _claude_auth_status(home):
    """Run `claude auth status` for `home` (None = main). The one seam tests stub."""
    env = dict(os.environ)
    if home is None:
        env.pop(ENV["claude"], None)
    else:
        env[ENV["claude"]] = str(home)
    try:
        out = subprocess.run(["claude", "auth", "status"], env=env, capture_output=True,
                             text=True, timeout=60)
        data = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return data if isinstance(data, dict) else None


def identity(kind, home):
    """{"email", "org"} of the login in `home` (None = main), or None when logged out."""
    if kind == "claude":
        status = _claude_auth_status(home)
        if not status or not status.get("loggedIn"):
            return None
        return {"email": status.get("email"), "org": status.get("orgId")}
    try:
        auth = json.loads(((Path(home) if home else main_home("codex")) / "auth.json").read_text())
        tokens = auth["tokens"]
        email = _jwt_payload(tokens["id_token"]).get("email")
        org = tokens.get("account_id")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return {"email": email, "org": org} if email else None


def running_homes():
    """Home path -> pids of this user's processes started with an extra home."""
    found = {}
    uid = os.getuid()
    wanted = {ENV[kind].encode() + b"=" for kind in KINDS}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            raw = (proc / "environ").read_bytes()
        except OSError:
            continue
        for item in raw.split(b"\0"):
            if any(item.startswith(prefix) for prefix in wanted):
                value = item.split(b"=", 1)[1].decode(errors="replace")
                if value:
                    found.setdefault(value, []).append(int(proc.name))
    return found


def _is_running(entry):
    home = str(entry["home"])
    running = running_homes()
    return bool(running.get(home) or running.get(str(Path(home).resolve())))


def _check_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SwitchrError(f"{name!r} is not a valid account name "
                           "(lowercase letters, digits and dashes, up to 24, starting with a letter or digit)")


def _check_kind(kind):
    if kind not in KINDS:
        raise SwitchrError(f"unknown kind {kind!r}; use one of {', '.join(KINDS)}")


def _taken(reg, kind, name):
    return name == reg["main"].get(kind, "main") or any(
        e.get("kind") == kind and e.get("name") == name for e in reg["accounts"])


def add(kind, name):
    _check_kind(kind)
    _check_name(name)
    reg = load_registry()
    if _taken(reg, kind, name):
        raise SwitchrError(f"a {kind} account named {name!r} already exists")
    home = data_dir() / kind / name
    _guard_home(kind, home)
    prepare_home(kind, home)
    entry = {"kind": kind, "name": name, "home": str(home), "pending": True}
    reg["accounts"].append(entry)
    save_registry(reg)
    return entry


def _drop(kind, name):
    """Delete an extra account's folder (links only, never what they point at) and entry."""
    reg = load_registry()
    for entry in reg["accounts"]:
        if entry.get("kind") == kind and entry.get("name") == name:
            home = _guard_home(kind, entry["home"])
            reg["accounts"].remove(entry)
            save_registry(reg)
            if home.is_dir() and not home.is_symlink():
                shutil.rmtree(home)
            return


def confirm(kind, name):
    entry = account(kind, name)
    if entry is None or entry.get("main"):
        raise SwitchrError(f"no extra {kind} account named {name!r}")
    pending = bool(entry.get("pending"))

    def refuse(why):
        if pending:
            _drop(kind, name)
        raise SwitchrError(why)

    found = identity(kind, entry["home"])
    if found is None:
        refuse(f"{kind} account {name!r} is not logged in")
    if found == identity(kind, None):
        refuse(f"{found['email']} is already the main {kind} account")
    for other in load_registry()["accounts"]:
        if other.get("kind") == kind and other.get("name") != name and not other.get("pending") \
                and identity(kind, other["home"]) == found:
            refuse(f"{found['email']} is already the {kind} account {other['name']!r}")
    reg = load_registry()
    for item in reg["accounts"]:
        if item.get("kind") == kind and item.get("name") == name:
            item.pop("pending", None)
            entry = item
    save_registry(reg)
    sync_programs()
    return entry


def rename(kind, old, new):
    _check_kind(kind)
    _check_name(new)
    reg = load_registry()
    if old == new:
        raise SwitchrError("the new name is the same as the old one")
    if _taken(reg, kind, new):
        raise SwitchrError(f"a {kind} account named {new!r} already exists")
    if old == reg["main"].get(kind, "main"):
        reg["main"][kind] = new
        save_registry(reg)
        return {"kind": kind, "name": new, "home": None, "main": True}
    for entry in reg["accounts"]:
        if entry.get("kind") == kind and entry.get("name") == old:
            old_home = _guard_home(kind, entry["home"])
            new_home = data_dir() / kind / new
            _guard_home(kind, new_home)
            if _is_running(entry):
                raise SwitchrError(f"{old!r} is in use by a running {kind}; close it first")
            if new_home.exists():
                raise SwitchrError(f"{new_home} already exists")
            if old_home.is_dir():
                os.rename(old_home, new_home)
            entry["name"], entry["home"] = new, str(new_home)
            save_registry(reg)
            sync_programs()
            return entry
    raise SwitchrError(f"no {kind} account named {old!r}")


def remove(kind, name):
    _check_kind(kind)
    entry = account(kind, name)
    if entry is None:
        raise SwitchrError(f"no {kind} account named {name!r}")
    if entry.get("main"):
        raise SwitchrError("the main account cannot be removed")
    _guard_home(kind, entry["home"])
    if _is_running(entry):
        raise SwitchrError(f"{name!r} is in use by a running {kind}; close it first")
    _drop(kind, name)
    sync_programs()


def _programs_path():
    base = os.environ.get("HERDR_APP_HOME")
    return (Path(base) if base else Path.home() / ".config" / "herdr-app") / "programs.json"


OWNED_KEY = re.compile(r"^switchr-(?:%s)-" % "|".join(KINDS))


def sync_programs():
    """Make the new-agent menu list every confirmed extra account, touching nothing else."""
    path = _programs_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as error:
        raise SwitchrError(f"cannot read {path}: {error}")
    if not isinstance(data, dict):
        raise SwitchrError(f"{path} is not a JSON object")
    table = data.get("programs")
    table = table if isinstance(table, dict) else {}
    table = {k: v for k, v in table.items() if not OWNED_KEY.match(k)}
    script = str(Path(__file__).resolve())
    for entry in load_registry()["accounts"]:
        if entry.get("pending"):
            continue
        kind, name = entry["kind"], entry["name"]
        table[f"switchr-{kind}-{name}"] = {
            "name": f"{kind.capitalize()} \u00b7 {name}", "run": [script, "run", kind, name]}
    data["programs"] = table
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".programs-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with_suppress_unlink(tmp)
        raise


def login(kind, name):
    """Add `name` if new, run the agent's own login in its home, then confirm."""
    _check_kind(kind)
    entry = account(kind, name)
    if entry is not None and entry.get("main"):
        raise SwitchrError("the main account is logged in through the agent itself, not Switchr")
    if entry is None:
        entry = add(kind, name)
    argv = ["claude", "auth", "login"] if kind == "claude" else ["codex", "login"]
    env = {**os.environ, ENV[kind]: entry["home"]}
    try:
        subprocess.run(argv, env=env)
    except OSError as error:
        raise SwitchrError(f"cannot start {argv[0]}: {error}")
    done = confirm(kind, name)
    return f"{kind} account {name!r} is logged in as {identity(kind, done['home'])['email']}"


# ------------------------------------------------------------------- limits

# Verified live 2026-11-13 (usage); renewal taken from the MIT-licensed
# rcosteira79/herdr-account-switch switcher.py and NOT yet verified live.
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_RENEW_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_RENEW_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CACHE_SECONDS = 120
RENEW_WITHIN = 300
LOCK_WAIT = 15


def _http(request):
    """The one seam for the network: returns the decoded JSON of a reply."""
    with urllib.request.urlopen(request, timeout=15) as reply:
        return json.loads(reply.read().decode())


def _cred_path(kind, entry):
    base = main_home(kind) if entry.get("main") else Path(entry["home"])
    return base / (".credentials.json" if kind == "claude" else "auth.json")


def _lock_path(kind, entry):
    if entry.get("main"):
        return data_dir() / f"main-{kind}.lock"
    return Path(entry["home"]) / ".switchr.lock"


@contextlib.contextmanager
def account_lock(kind, entry, wait=LOCK_WAIT):
    """Hold the account's lock; blocks up to `wait` seconds, then refuses."""
    path = _lock_path(kind, entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a")
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SwitchrError(f"{kind} account {entry['name']!r} is busy (lock held)")
                time.sleep(0.1)
        yield
    finally:
        handle.close()  # closing releases the flock


def main_agent_running(kind):
    """True when a claude/codex process of this user runs on main (variable unset)."""
    uid, me = os.getuid(), os.getpid()
    var = ENV[kind].encode() + b"="
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == me:
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            argv0 = (proc / "cmdline").read_bytes().split(b"\0")[0]
            if os.path.basename(argv0.decode(errors="replace")) != AGENT[kind]:
                continue
            env = (proc / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        if not any(item.startswith(var) and len(item) > len(var) for item in env):
            return True
    return False


def _agent_running(kind, entry):
    if entry.get("main"):
        return main_agent_running(kind)
    return _is_running(entry)


def _read_credentials(kind, entry):
    """(raw file dict, access token, refresh token, expiry in seconds, account id) or None."""
    try:
        raw = json.loads(_cred_path(kind, entry).read_text())
        if kind == "claude":
            oauth = raw["claudeAiOauth"]
            return (raw, oauth["accessToken"], oauth.get("refreshToken"),
                    float(oauth["expiresAt"]) / 1000, None)
        tokens = raw["tokens"]
        access = tokens["access_token"]
        exp = _jwt_payload(access).get("exp")
        return raw, access, tokens.get("refresh_token"), float(exp) if exp else None, tokens.get("account_id")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_credentials(path, raw):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cred-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(raw, handle, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with_suppress_unlink(tmp)
        raise


def _post_json(url, body, headers):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers})
    return _http(request)


def _renew(kind, entry, creds):
    """Exchange the refresh token for a new pair and store it. True on success."""
    raw, _access, refresh, _exp, _account_id = creds
    if not refresh:
        return False
    try:
        if kind == "claude":
            scopes = raw["claudeAiOauth"].get("scopes") or []
            reply = _post_json(CLAUDE_RENEW_URL, {
                "grant_type": "refresh_token", "refresh_token": refresh,
                "client_id": CLAUDE_CLIENT_ID, "scope": " ".join(scopes)},
                {"User-Agent": "axios/1.15.2", "Accept": "application/json, text/plain, */*"})
            oauth = raw["claudeAiOauth"]
            oauth["accessToken"] = reply["access_token"]
            oauth["refreshToken"] = reply.get("refresh_token", refresh)
            oauth["expiresAt"] = int((time.time() + float(reply["expires_in"])) * 1000)
        else:
            reply = _post_json(CODEX_RENEW_URL, {
                "client_id": CODEX_CLIENT_ID, "grant_type": "refresh_token",
                "refresh_token": refresh, "scope": "openid profile email"},
                {"Accept": "application/json"})
            tokens = raw["tokens"]
            tokens["access_token"] = reply["access_token"]
            tokens["refresh_token"] = reply.get("refresh_token", refresh)
            if reply.get("id_token"):
                tokens["id_token"] = reply["id_token"]
            raw["last_refresh"] = datetime.now(timezone.utc).isoformat()
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        return False
    _write_credentials(_cred_path(kind, entry), raw)
    return True


def _cache_path():
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "switchr" / "usage.json"


def _cache_load():
    try:
        data = json.loads(_cache_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_store(key, value):
    data = _cache_load()
    data[key] = {"at": time.time(), "value": value}
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".usage-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except BaseException:
        with_suppress_unlink(tmp)
        raise


def _blank(state):
    return {"session": None, "week": None, "resets": None, "state": state}


def _percent(value):
    return int(round(value)) if isinstance(value, (int, float)) else None


def _parse_claude(reply):
    limits = [i for i in reply.get("limits") or [] if isinstance(i, dict)]
    session = [i for i in limits if i.get("group") == "session" and i.get("percent") is not None]
    weekly = [i for i in limits if i.get("group") == "weekly" and i.get("percent") is not None]
    top = max(weekly, key=lambda i: i["percent"], default=None)
    return {"session": _percent(session[0]["percent"]) if session else None,
            "week": _percent(top["percent"]) if top else None,
            "resets": top.get("resets_at") if top else None, "state": "ok"}


def _parse_codex(reply):
    window = reply.get("rate_limit") or {}
    primary = window.get("primary_window") or {}
    secondary = window.get("secondary_window") or {}
    reset = secondary.get("reset_at")
    resets = datetime.fromtimestamp(reset, timezone.utc).isoformat() if isinstance(reset, (int, float)) else None
    return {"session": _percent(primary.get("used_percent")),
            "week": _percent(secondary.get("used_percent")), "resets": resets, "state": "ok"}


def usage(kind, name, renew=True):
    """What is left on an account: session %, week %, when the week resets, and a state."""
    _check_kind(kind)
    entry = account(kind, name)
    if entry is None:
        raise SwitchrError(f"no {kind} account named {name!r}")
    key = f"{kind}/{name}"
    cached = _cache_load().get(key)
    if cached and time.time() - cached.get("at", 0) < CACHE_SECONDS:
        return cached["value"]
    creds = _read_credentials(kind, entry)
    if creds is None:
        return _blank("logged-out")
    expiry = creds[3]
    if expiry is not None and expiry - time.time() < RENEW_WITHIN:
        if not renew or _agent_running(kind, entry):
            return _blank("unknown")
        try:
            with account_lock(kind, entry):
                creds = _read_credentials(kind, entry)  # another renewal may have won
                if creds is None:
                    return _blank("logged-out")
                if creds[3] is not None and creds[3] - time.time() < RENEW_WITHIN:
                    if not _renew(kind, entry, creds):
                        return _blank("unknown")
                    creds = _read_credentials(kind, entry)
        except SwitchrError:
            return _blank("unknown")
        if creds is None:
            return _blank("unknown")
    _raw, access, _refresh, _exp, account_id = creds
    try:
        if kind == "claude":
            reply = _http(urllib.request.Request(CLAUDE_USAGE_URL, headers={
                "Authorization": f"Bearer {access}", "anthropic-beta": "oauth-2025-04-20"}))
            value = _parse_claude(reply)
        else:
            reply = _http(urllib.request.Request(CODEX_USAGE_URL, headers={
                "Authorization": f"Bearer {access}", "chatgpt-account-id": str(account_id),
                "User-Agent": "codex-cli", "Accept": "application/json"}))
            value = _parse_codex(reply)
    except (OSError, ValueError, AttributeError, TypeError, urllib.error.URLError):
        return cached["value"] if cached else _blank("unknown")
    _cache_store(key, value)
    return value


def list_accounts():
    """Every account, main first per kind, each with its usage."""
    reg = load_registry()
    out = []
    for kind in KINDS:
        out.append((kind, reg["main"].get(kind, "main"), True, False))
        for entry in reg["accounts"]:
            if entry.get("kind") == kind:
                out.append((kind, entry["name"], False, bool(entry.get("pending"))))
    return [{"kind": k, "name": n, "main": m, "pending": p,
             "usage": _blank("logged-out") if p else usage(k, n)} for k, n, m, p in out]


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
    try:
        with account_lock(kind, entry):  # wait out a renewal; released before exec
            pass
    except SwitchrError as error:
        raise SystemExit(f"switchr: {error}")
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
    add_cmd = sub.add_parser("add", help="create an account folder (not yet logged in)")
    add_cmd.add_argument("kind", choices=KINDS)
    add_cmd.add_argument("name")
    login_cmd = sub.add_parser("login", help="add if needed, log in, then confirm")
    login_cmd.add_argument("kind", choices=KINDS)
    login_cmd.add_argument("name")
    rename_cmd = sub.add_parser("rename", help="rename an account (main too)")
    rename_cmd.add_argument("kind", choices=KINDS)
    rename_cmd.add_argument("old")
    rename_cmd.add_argument("new")
    remove_cmd = sub.add_parser("remove", help="delete an extra account")
    remove_cmd.add_argument("kind", choices=KINDS)
    remove_cmd.add_argument("name")
    list_cmd = sub.add_parser("list", help="list accounts with what is left on each")
    list_cmd.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    options = build_parser().parse_args(argv)
    if options.command == "run":
        args = options.args
        if args and args[0] == "--":
            args = args[1:]
        run(options.kind, options.name, args)
        return
    try:
        if options.command == "list":
            rows = list_accounts()
            if options.json:
                print(json.dumps(rows))
            else:
                for r in rows:
                    u = r["usage"]
                    print(f"{r['kind']:7}{r['name']:24}{u['state']:11}session {u['session']}  week {u['week']}")
        elif options.command == "add":
            entry = add(options.kind, options.name)
            print(f"switchr: created {options.kind} account {entry['name']!r}; "
                  f"log in with: switchr login {options.kind} {entry['name']}")
        elif options.command == "login":
            print("switchr:", login(options.kind, options.name))
        elif options.command == "rename":
            rename(options.kind, options.old, options.new)
            print(f"switchr: {options.kind} account {options.old!r} is now {options.new!r}")
        elif options.command == "remove":
            remove(options.kind, options.name)
            print(f"switchr: removed {options.kind} account {options.name!r}")
    except SwitchrError as error:
        return f"switchr: {error}"


if __name__ == "__main__":
    sys.exit(main())
