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
