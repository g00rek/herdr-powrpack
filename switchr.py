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
import concurrent.futures
import contextlib
import fcntl
import http.client
import re
import shlex
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
    """Refuse any home but exactly <data dir>/<kind>/<valid name>: never a main
    folder, never the data directory or a kind's folder (which hold every account)."""
    path = Path(home).resolve()
    data = data_dir().resolve()
    for other in KINDS:
        main = main_home(other).resolve()
        if path == main or main in path.parents:
            raise SwitchrError(f"refusing {path}: it is (or is inside) the main {other} folder")
    if kind not in KINDS or path.parent != data / kind or not NAME_RE.match(path.name):
        raise SwitchrError(f"refusing {path}: not an account folder in {data / kind}")
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


FORKED = ".switchr-forked"


def _set_aside(home, link):
    """Move a real file or folder that took a shared link's place into
    <home>/.switchr-forked/<name>.<time>, so the link can be made again."""
    folder = home / FORKED
    folder.mkdir(mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = folder / f"{link.name}.{stamp}"
    n = 1
    while os.path.lexists(target):
        n += 1
        target = folder / f"{link.name}.{stamp}-{n}"
    os.rename(link, target)
    print(f"switchr: {link.name} in {home} was a copy instead of the shared one; "
          f"kept it as {target} and shared the main one again", file=sys.stderr)


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
            elif os.path.lexists(link):
                # An agent saved a shared file by writing a new one over the
                # link (Claude's settings.json through /config, Codex's
                # config.toml). Left alone it would differ from main forever.
                _set_aside(home, link)
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
    return {**entry, "email": found["email"]}  # the identity seen, so callers need not ask again


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
    if entry is not None and not entry.get("pending"):
        raise SwitchrError(f"{name} is already logged in; remove it first to log in again.")
    if entry is None:
        entry = add(kind, name)
    argv = ["claude", "auth", "login"] if kind == "claude" else ["codex", "login"]
    env = {**os.environ, ENV[kind]: entry["home"]}
    try:
        subprocess.run(argv, env=env)
    except OSError as error:
        raise SwitchrError(f"cannot start {argv[0]}: {error}")
    done = confirm(kind, name)
    return f"{kind} account {name!r} is logged in as {done['email']}"


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
# Per request. `list` asks every account at once, so a hung network costs
# about this long (twice with a renewal), not this long per account.
HTTP_TIMEOUT = 4


def _http(request):
    """The one seam for the network: returns the decoded JSON of a reply."""
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as reply:
        return json.loads(reply.read().decode())


def _cred_path(kind, entry):
    base = main_home(kind) if entry.get("main") else Path(entry["home"])
    return base / (".credentials.json" if kind == "claude" else "auth.json")


def _lock_path(kind, entry):
    return Path(entry["home"]) / ".switchr.lock"


@contextlib.contextmanager
def account_lock(kind, entry, wait=LOCK_WAIT):
    """Hold the account's lock; blocks up to `wait` seconds, then refuses."""
    try:
        path = _lock_path(kind, entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a")
    except OSError as error:
        raise SwitchrError(f"cannot open the lock for {kind} account {entry['name']!r}: {error}")
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


def _main_credentials():
    return {os.path.realpath(main_home("claude") / ".credentials.json"),
            os.path.realpath(main_home("codex") / "auth.json")}


def _write_credentials(path, raw):
    # Switchr never rewrites a main login, even through a link to it.
    if os.path.realpath(path) in _main_credentials():
        raise SwitchrError(f"refusing to write {path}: it is a main account's login")
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


def _lifetime(reply):
    try:
        seconds = float(reply.get("expires_in", 3600))
    except (TypeError, ValueError):
        return 3600.0
    return seconds if seconds > 0 else 3600.0


def _renew(kind, entry, creds):
    """Exchange the refresh token for a new pair and store it. True on success."""
    raw, _access, refresh, _exp, _account_id = creds
    if not refresh or entry.get("main"):
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
            oauth["expiresAt"] = int((time.time() + _lifetime(reply)) * 1000)
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
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, http.client.HTTPException):
        return False
    try:
        _write_credentials(_cred_path(kind, entry), raw)
    except (OSError, SwitchrError):
        return False
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
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Accounts are asked at once (threads, and the panel and menu as separate
    # processes): read, change and write under one lock, or keys get lost.
    with open(path.parent / ".usage.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = _cache_load()
        data[key] = {"at": time.time(), "value": value}
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
        # Switchr never rewrites the main login; extras only while no agent runs.
        if not renew or entry.get("main") or _is_running(entry):
            return _blank("unknown")
        try:
            with account_lock(kind, entry):
                creds = _read_credentials(kind, entry)  # another renewal may have won
                if creds is None:
                    return _blank("logged-out")
                if creds[3] is not None and creds[3] - time.time() < RENEW_WITHIN:
                    if _is_running(entry):  # an agent started after the first check
                        return _blank("unknown")
                    if not _renew(kind, entry, creds):
                        return _blank("unknown")
                    creds = _read_credentials(kind, entry)
        except (SwitchrError, OSError):
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
    except (OSError, ValueError, AttributeError, TypeError, urllib.error.URLError,
            http.client.HTTPException):
        return cached["value"] if cached else _blank("unknown")
    try:
        _cache_store(key, value)
    except OSError:
        pass
    return value


def list_accounts(with_usage=True):
    """Every account, main first per kind, each with its usage.

    Without usage nothing goes to the network and `usage` is None. With it,
    every account is asked at once, so one hung account does not hold up the rest."""
    reg = load_registry()
    out = []
    for kind in KINDS:
        out.append((kind, reg["main"].get(kind, "main"), True, False))
        for entry in reg["accounts"]:
            if entry.get("kind") == kind:
                out.append((kind, entry["name"], False, bool(entry.get("pending"))))

    def safe(row):
        k, n, _m, p = row
        if p:
            return _blank("logged-out")
        try:
            return usage(k, n)
        except Exception:
            return _blank("unknown")
    if not with_usage:
        found = [None] * len(out)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(out))) as pool:
            found = list(pool.map(safe, out))
    return [{"kind": k, "name": n, "main": m, "pending": p, "usage": u}
            for (k, n, m, p), u in zip(out, found)]


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
    if not entry.get("main"):  # Switchr never renews main, so only extras can race
        try:
            prepare_home(kind, Path(entry["home"]))
            with account_lock(kind, entry):  # wait out a renewal; released before exec
                pass
        except (SwitchrError, OSError) as error:
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


# ------------------------------------------------------- terminals and moves

# Quit command typed into an idle agent. Verified live 2026-11-13 through
# `hc send` into throwaway terminals: Claude Code 2.1.323 exits on `/exit`;
# codex-cli 0.168.0 exits on `/quit` (and prints "codex resume <uuid>").
# `hc send` run from inside a Herdr terminal wraps the text as
# `<hc from="tN">…</hc>`, which the agent reads as a prompt, not a command;
# without HC_TERMINAL_ID in the environment it types the text as is.
QUIT = {"claude": "/exit", "codex": "/quit"}
SHELLS = {"bash", "zsh", "fish", "sh", "dash"}
HC_AGENTS = {"claude-code": "claude", "codex": "codex"}
QUIT_WAIT = 20
LAUNCH_WAIT = 30
POLL = 0.25

# Codex session of a running pid, verified live 2026-11-13 on codex-cli
# 0.168.0: no rollout exists until the first message; afterwards the process
# keeps `<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-<time>-<uuid>.jsonl` open,
# and `/quit` printed that same uuid as the one to resume. A process with
# subagents holds their rollouts open too; their first line (`session_meta`)
# carries `parent_thread_id` and a `source` of {"subagent": …}, so they are
# skipped and the newest remaining file wins.
ROLLOUT_RE = re.compile(r"/rollout-[^/]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")

_clock = time.monotonic
_sleep = time.sleep


def _hc(args):
    """The one seam for Herdr: run `hc <args> --json` and return its JSON reply."""
    env = {k: v for k, v in os.environ.items() if k != "HC_TERMINAL_ID"}
    try:
        out = subprocess.run(["hc", *args, "--json"], env=env, capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise SwitchrError(f"cannot reach Herdr: {error}")
    if out.returncode != 0:
        text = (out.stderr or out.stdout).strip()
        try:
            text = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise SwitchrError(text or f"hc {args[0]} failed")
    try:
        return json.loads(out.stdout)
    except ValueError:
        raise SwitchrError(f"hc {args[0]} gave an unreadable reply")


def _proc_path(pid, leaf):
    return Path("/proc") / str(pid) / leaf


def _program(argv0):
    return os.path.basename(str(argv0 or ""))


def agent_kind(foreground):
    """claude / codex for a terminal's foreground program, else None."""
    if not isinstance(foreground, dict):
        return None
    argv = foreground.get("argv") or [None]
    for candidate in (foreground.get("name"), _program(argv[0])):
        if candidate in AGENT.values():
            return next(k for k, v in AGENT.items() if v == candidate)
    return None


def _is_shell(foreground):
    if not isinstance(foreground, dict):
        return True  # nothing in the foreground
    argv = foreground.get("argv") or [None]
    names = (foreground.get("name"), _program(argv[0]))
    return any(isinstance(n, str) and n.lstrip("-") in SHELLS for n in names)


def account_of(pid, kind=None):
    """(kind, account name) of a running agent, from its environment; None if unreadable."""
    try:
        raw = _proc_path(pid, "environ").read_bytes()
        if kind is None:
            argv0 = _proc_path(pid, "cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
            kind = agent_kind({"argv": [argv0]})
    except OSError:
        return None
    if kind not in KINDS:
        return None
    prefix = ENV[kind].encode() + b"="
    value = ""
    for item in raw.split(b"\0"):
        if item.startswith(prefix):
            value = item[len(prefix):].decode(errors="replace")
    reg = load_registry()
    if value:
        wanted = os.path.realpath(value)
        for entry in reg["accounts"]:
            if entry.get("kind") == kind and os.path.realpath(entry["home"]) == wanted:
                return kind, entry["name"]
    return kind, reg["main"].get(kind, "main")


def which():
    """Address -> kind, account, main and status of every live Claude / Codex terminal."""
    out = {}
    listing = _hc(["ls"])
    terminals = listing.get("terminals", []) if isinstance(listing, dict) else listing
    for item in terminals or []:
        if item.get("agent") not in HC_AGENTS:
            continue
        address = item.get("address")
        try:
            info = _hc(["get", address])
        except SwitchrError:
            continue
        foreground = info.get("foreground") or {}
        kind = agent_kind(foreground) or HC_AGENTS[item["agent"]]
        pid = foreground.get("pid")
        found = account_of(pid, kind) if pid else None
        if found is None:
            continue
        name = found[1]
        out[address] = {"kind": kind, "account": name,
                        "main": name == load_registry()["main"].get(kind, "main"),
                        "state": (info.get("status") or {}).get("state")}
    return out


# Options of the installed CLIs, taken from `claude --help` (Claude Code
# 2.1.323) and `codex --help` / `codex resume --help` (codex-cli 0.168.0) on
# 2026-11-13. VALUE takes one value, MANY takes every following word up to the
# next option, MAYBE takes one optional value (Claude's parser then takes a
# following word as that value). DROP is removed with its value(s), because
# it would start or pick another conversation than the one being moved.
# A word that is no option's value is a prompt (or Codex's `resume [ID]`): it
# is already in the conversation and is dropped, never sent again.
CLI_OPTIONS = {
    "claude": {
        "VALUE": {"--agent", "--agents", "--append-system-prompt", "--autocompact", "--debug-file",
                  "--effort", "--environment", "--fallback-model", "--input-format", "--json-schema",
                  "--max-budget-usd", "--model", "-n", "--name", "--output-format", "--permission-mode",
                  "--permission-prompts", "--plugin-dir", "--plugin-url",
                  "--remote-control-session-name-prefix", "--session-id", "--setting-sources",
                  "--settings", "--system-prompt", "--system-prompt-snapshot"},
        "MANY": {"--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--disallowedTools",
                 "--disallowed-tools", "--file", "--mcp-config", "--tools"},
        "MAYBE": {"--cloud", "-d", "--debug", "--from-pr", "--prompt-suggestions", "--remote-control",
                  "-r", "--resume", "--teleport", "-w", "--worktree"},
        "FLAG": {"--allow-dangerously-skip-permissions", "--ax-screen-reader", "--bg", "--background",
                 "--bare", "--brief", "--chrome", "-c", "--continue", "--dangerously-skip-permissions",
                 "--desktop", "--disable-slash-commands", "--exclude-dynamic-system-prompt-sections",
                 "--fork-session", "--forward-subagent-text", "-h", "--help", "--ide",
                 "--include-hook-events", "--include-partial-messages", "--no-chrome",
                 "--no-session-persistence", "-p", "--print", "--replay-user-messages", "--restricted",
                 "--safe-mode", "--strict-mcp-config", "--tmux", "--verbose", "-v", "--version"},
        "DROP": {"-r", "--resume", "-c", "--continue", "--session-id", "--from-pr", "--fork-session",
                 "--teleport"},
    },
    "codex": {
        "VALUE": {"-c", "--config", "--enable", "--disable", "--remote", "--remote-auth-token-env",
                  "-m", "--model", "--local-provider", "-p", "--profile", "-s", "--sandbox",
                  "-C", "--cd", "--add-dir", "-a", "--ask-for-approval"},
        "MANY": {"-i", "--image"},
        "MAYBE": set(),
        "FLAG": {"--strict-config", "--oss", "--approve-for-me",
                 "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
                 "--worktree", "--search", "--no-alt-screen", "--no-daemon", "-h", "--help",
                 "-V", "--version", "--last", "--all", "--include-non-interactive"},
        "DROP": {"--last", "--all"},
    },
}


def resume_argv(kind, argv, session):
    """Arguments to start `kind` again on `session`: the original options minus
    any resume and minus prompts. Refuses (SwitchrError) when an unknown option
    is followed by a word, since that word may be its value or a prompt."""
    table = CLI_OPTIONS[kind]
    args = list(argv[1:])
    kept = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            break  # everything after is a prompt
        if not arg.startswith("-") or arg == "-":
            continue  # a prompt, or codex's `resume [ID]`
        name, has_value = arg.split("=", 1)[0], "=" in arg
        values = []
        if not has_value:
            if name in table["VALUE"]:
                values, i = args[i:i + 1], i + 1
            elif name in table["MANY"]:
                while i < len(args) and not args[i].startswith("-"):
                    values.append(args[i])
                    i += 1
            elif name in table["MAYBE"]:
                if i < len(args) and not args[i].startswith("-"):
                    values, i = [args[i]], i + 1
            elif name not in table["FLAG"] and i < len(args) and not args[i].startswith("-"):
                raise SwitchrError(
                    f"it was started with {name}, an option Switchr does not know, followed by "
                    f"{args[i]!r}; Switchr cannot tell whether that is the option's value or a prompt")
        if name not in table["DROP"]:
            kept += [arg, *values]
    return [*kept, "--resume", session] if kind == "claude" else ["resume", session, *kept]


def _claude_session_id(pid):
    """sessionId from Claude's own record of a running pid (sessions/ is shared by all homes)."""
    try:
        data = json.loads((main_home("claude") / "sessions" / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    value = data.get("sessionId") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def _codex_rollout_is_main(path):
    try:
        with open(path) as handle:
            first = json.loads(handle.readline())
        payload = first.get("payload") or {}
    except (OSError, ValueError, AttributeError):
        return False
    source = payload.get("source")
    return not payload.get("parent_thread_id") and not (isinstance(source, dict) and "subagent" in source)


def session_of(kind, pid):
    """The conversation a running agent is in, or None when it has none worth resuming."""
    if kind == "claude":
        session = _claude_session_id(pid)
        if not session:
            return None
        # A session nobody has written to yet has no transcript and cannot be resumed.
        projects = main_home("claude") / "projects"
        if not any(projects.glob(f"*/{session}.jsonl")):
            return None
        return session
    try:
        fds = list(_proc_path(pid, "fd").iterdir())
    except OSError:
        return None
    found = []
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        match = ROLLOUT_RE.search(target)
        if not match or not _codex_rollout_is_main(target):
            continue
        try:
            found.append((os.stat(target).st_mtime, match.group(1)))
        except OSError:
            continue
    return max(found)[1] if found else None


def _result(ok, message):
    return {"ok": ok, "message": message}


def _wait(address, done, seconds):
    """Poll `hc get` until done(info) is true: (True, info), or (False, last info) on timeout."""
    deadline = _clock() + seconds
    while True:
        info = _hc(["get", address])
        if done(info):
            return True, info
        if _clock() >= deadline:
            return False, info
        _sleep(POLL)


def move(address, name):
    """Quit the idle agent in `address` and start it again on account `name`, same conversation."""
    try:
        info = _hc(["get", address])
    except SwitchrError as error:
        return _result(False, f"Could not read that terminal: {error}")
    foreground = info.get("foreground") or {}
    kind = agent_kind(foreground)
    if kind is None:
        return _result(False, "No Claude Code or Codex is running in that terminal.")
    state = (info.get("status") or {}).get("state")
    if state == "working":
        return _result(False, "Still working — try when it has finished.")
    if state == "blocked":
        return _result(False, "It is waiting for an answer — reply to it first, then move it.")
    if state not in ("idle", "done"):
        return _result(False, "Cannot tell whether it is idle, so it was left alone.")
    target = account(kind, name)
    if target is None:
        return _result(False, f"There is no {kind} account called {name}.")
    if target.get("pending"):
        return _result(False, f"{name} is not logged in yet.")
    pid = foreground.get("pid")
    current = account_of(pid, kind) if pid else None
    if current is None:
        return _result(False, "Could not tell which account it is on, so it was left alone.")
    if current[1] == name:
        return _result(False, f"It is already on {name}.")
    session = session_of(kind, pid)
    if not session:
        return _result(False, "There is no conversation to carry over yet, so nothing was changed.")
    try:
        args = resume_argv(kind, foreground.get("argv") or [AGENT[kind]], session)
    except SwitchrError as error:
        return _result(False, f"Not moved: {error}. Nothing was changed.")
    line = shlex.join([str(Path(__file__).resolve()), "run", kind, name, "--", *args])
    try:
        _hc(["send", address, QUIT[kind]])
        quit_ok, info = _wait(address, lambda i: _is_shell(i.get("foreground")), QUIT_WAIT)
    except SwitchrError as error:
        return _result(False, f"Could not quit the agent: {error}")
    if not quit_ok:
        still = info.get("foreground") or {}
        if agent_kind(still) == kind and still.get("pid") == pid:
            # Not "nothing was changed": with unsent text in the input box the
            # quit command is added to it, or sent as a message. No input-clearing
            # key is sent first: none is verified safe in both agents (ctrl+u
            # clears a bash line through `hc keys`, but in Claude Code it is
            # also a scroll key and clears one line at most of a draft).
            return _result(False, f"The agent did not quit within {QUIT_WAIT} seconds and is still "
                                  f"running on its old account. Switchr typed {QUIT[kind]} into it, "
                                  "which may have been added to unsent text in its input box or sent "
                                  "as a message; check the terminal.")
        return _result(False, "The agent quit, but Switchr did not recognise the program now running "
                              f"in the terminal, so it started nothing. To continue on {name}, run: {line}")
    if info.get("state") == "finished":
        return _result(False, "The terminal closed when the agent quit. To continue on "
                              f"{name}, run in a new terminal: {line}")

    def arrived(i):
        fg = i.get("foreground") or {}
        if agent_kind(fg) != kind or not fg.get("pid"):
            return False
        if (account_of(fg["pid"], kind) or (None, None))[1] != name:
            return False
        return kind != "claude" or _claude_session_id(fg["pid"]) == session

    try:
        _hc(["send", address, line])
        arrived_ok, _info = _wait(address, arrived, LAUNCH_WAIT)
    except SwitchrError as error:
        return _result(False, f"The agent quit but could not be started again: {error}. "
                              f"To continue, run: {line}")
    if not arrived_ok:
        return _result(False, f"Started on {name}, but could not confirm the same conversation "
                              f"within {LAUNCH_WAIT} seconds; check the terminal.")
    return _result(True, f"Moved to {name}, same conversation.")


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
    list_cmd.add_argument("--no-usage", action="store_true",
                          help="names only: no network, no limits")
    which_cmd = sub.add_parser("which", help="which account each agent terminal is on")
    which_cmd.add_argument("--json", action="store_true")
    move_cmd = sub.add_parser("move", help="move an idle agent to another account, same conversation")
    move_cmd.add_argument("address")
    move_cmd.add_argument("name")
    move_cmd.add_argument("--json", action="store_true")
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
            rows = list_accounts(with_usage=not options.no_usage)
            if options.json:
                print(json.dumps(rows))
            else:
                for r in rows:
                    u = r["usage"]
                    if u is None:
                        print(f"{r['kind']:7}{r['name']}")
                    else:
                        print(f"{r['kind']:7}{r['name']:24}{u['state']:11}session {u['session']}  week {u['week']}")
        elif options.command == "which":
            found = which()
            if options.json:
                print(json.dumps(found))
            else:
                for address, row in found.items():
                    print(f"{address:8}{row['kind']:7}{row['account']:24}{row['state']}")
        elif options.command == "move":
            result = move(options.address, options.name)
            print(json.dumps(result) if options.json else f"switchr: {result['message']}")
            return 0 if result["ok"] else 1
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
