// Switchr's account rows, kept apart from the panel so they can be tested
// without Herdr.

/** What is left on an account, in one short line. */
export function usageLine(account) {
  const u = account?.usage;
  if (account?.pending || u?.state === "logged-out") return "Not logged in";
  if (u == null) return "Checking limits…";
  if (u.state !== "ok" || u.session == null || u.week == null) return "limit unknown";
  return `5 h ${u.session}% · week ${u.week}%`;
}

/**
 * Accounts are listed in two steps: names at once (`list --no-usage`, no
 * network), limits when they come (`list`, which asks every account over the
 * network and can take seconds). This lays the limits found in `source` over
 * `rows`; a row with none keeps the one it had, else gets `missing`.
 */
export function withUsage(rows, source, missing) {
  const found = new Map((Array.isArray(source) ? source : [])
    .filter((a) => a && a.usage)
    .map((a) => [`${a.kind}/${a.name}`, a.usage]));
  return rows.map((a) => ({ ...a, usage: found.get(`${a.kind}/${a.name}`) ?? a.usage ?? missing }));
}

export const UNKNOWN = { state: "unknown" };

// The colours an account's badge can wear, in the order the panel offers them
// (null: the theme's accent). Named after the terminal's six, so a theme
// change repaints them; the agents list maps them the same way.
export const COLORS = [null, "red", "yellow", "green", "cyan", "blue", "magenta"];
const ANSI = { red: "ansi1", green: "ansi2", yellow: "ansi3", blue: "ansi4", magenta: "ansi5", cyan: "ansi6" };

/** The paint for a colour name in theme `t`; the accent for none or an unknown one. */
export function paint(t, color) {
  const slot = ANSI[color];
  return (slot && t[slot]) || t.accent;
}
