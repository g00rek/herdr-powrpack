# Browsr: a crash log

Decided with Michał, 2026-11-13. Browsr's browser "goes down fairly often" and
nothing records why: `chromium.log` is truncated on every launch, so the evidence
is gone the moment Browsr comes back.

## What he gets

- Every time the Browsr browser goes away without Browsr asking it to, one entry
  is written to `${XDG_STATE_HOME:-~/.local/state}/herdr-dev-browser/crashes.jsonl`
  (state dir = `STATE_DIR` in bridge.py): when, the browser pid, how it ended,
  and the last 40 lines of that launch's Chromium log.
- "How it ended", in plain words, from what the system recorded about that pid:
  - `killed: out of memory` — kernel log has `Killed process <pid>` (`journalctl -k`);
  - `crashed: <signal>` — systemd-coredump recorded it (`coredumpctl list <pid>`
    / journal `Process <pid> (chromium) ... dumped core`), signal name included;
  - `closed or ended without a trace` — none of the above.
- Tab crashes count too: a coredump of a renderer whose command line carries
  Browsr's `--user-data-dir` is logged as `tab crashed: <signal>` (checked on the
  same cadence, each dump logged once).
- Deliberate stops are not crashes: Restart, switching Browsr off, and `quit`
  leave an intent mark first; a disappearance within 30 s of that mark is not
  logged.
- Logs are kept, not overwritten: the previous `chromium.log` is moved to
  `logs/chromium-<YYYYmmdd-HHMMSS>.log` at each launch; the newest 10 are kept.
- `bridge.py crashes` prints the last 10 entries as plain lines
  (`13 Nov 10:42 — killed: out of memory`) followed by where the file is.
- The Powr panel's Browsr status shows the latest one when it happened in the
  last 24 h: "Last went down 10:42 — out of memory."

## Constraints

- Detection rides on the existing health watch loop (`watch_health`, every
  `HEALTH_POLL_SECONDS`); no new daemon. Only the watcher that holds the health
  lock writes entries.
- `journalctl` / `coredumpctl` may be missing or unreadable: then the entry says
  `closed or ended without a trace` and nothing fails.
- No network, nothing leaves the machine. Public repo: generic, no personal data.
- Tests in `tests/test_bridge.py` style with `sandboxed_state()`; system log
  lookups behind one seam, stubbed.

## Left to the builder

The exact JSON field names, how the pid of the running browser is remembered
between polls, and how the panel line is fetched (a `bridge.py --app status`
field is the obvious place).

## Follow-up: falls with no trace (2026-11-13)

A fall on 12 Nov left no kernel kill and no core dump; the machine was short of
memory and the browser had stopped answering minutes earlier.

- Every browser entry carries a memory snapshot taken when the disappearance is
  seen (`memory`: MemAvailable, MemTotal, swap used in MB; `some`/`full` avg60
  from `/proc/pressure/memory`; missing files leave the fields out). Read
  through one seam, `memory_snapshot`.
- No-trace + memory short (`full` avg60 >= 10, or MemAvailable < 5% of
  MemTotal) is logged as "ended without a trace while memory was short". A
  later coredump/OOM record still overwrites it.
- If the browser had stopped answering while still alive, the entry records
  `unanswered_seconds`, and `bridge.py crashes` appends "— stopped answering
  2 min before". The watch loop converts the health watch's start time to wall
  clock and hands it to the crash watch, which remembers it per poll (the
  health watch has already forgotten by the time the browser is gone).
- Lines stay one short sentence; the memory note is the reason itself, so no
  extra text is added (and none when a coredump/OOM cause exists).
- The snapshot is taken after the browser is gone (up to one poll, 10 s, later),
  so MemAvailable may already have recovered; `full` avg60 is the reliable
  signal. An outage start only counts from when the current browser pid first
  appeared, so a relaunch never inherits the previous browser's outage.
