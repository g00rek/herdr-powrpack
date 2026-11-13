import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex, InputState, Input } from "gpui-base";
import { exec, focused, spaces, theme, home, appHome, notify, run, props, shared, setShared,
	closeOverlay, layout, openOverlay, readTextFile, writeTextFile } from "herdr";
import { swapMod } from "./layout-swap.js";

// Powr: one button in the chrome and one panel. The panel switches the
// modules of the pack on and off and shows a page for each.
//   { "role": "button" }  a square in the chrome that opens the panel
//   anything else         the panel

// Where Browsr is checked out. Change this line if yours is elsewhere.
const BRIDGE = `${home()}/Projects/herdr-powrpack/bridge.py`;
// Where Switchr is checked out, next to Browsr.
const SWITCHR = `${home()}/Projects/herdr-powrpack/switchr.py`;
const MODULES = "powr.modules";
const PANEL = "powr.panel";
const SAME_PRESS = 250;
const STATUS_EVERY = 4000;

// Where Browsr leaves a question and reads the answer. A file, because the
// two are different programs: one is a mod in this window, the other is a
// script an agent runs in a terminal somewhere else. The button is on screen
// all day and exists once, so it is the one that listens.
const BROWSR_STATE = `${home()}/.local/state/herdr-dev-browser`;
const ASK = `${BROWSR_STATE}/ask.json`;
const ANSWER = `${BROWSR_STATE}/answer.json`;
const ASK_EVERY = 1000;

// Which projects have a browser: { [spaceId]: true }. Read by bridge.py too.
const ON = "browsr.on";
// Settings, kept where the app keeps what its panels remember.
const NEW_ON = "browsr.new-on";       // new spaces and worktrees get a browser
const WHEN_ASKED = "browsr.when-asked"; // "ask" | "on" | "never"
const FOLD = "browsr.fold";           // other groups fold on a switch
const LINKS = "browsr.links";         // links clicked in Herdr open in Browsr
const WASH = "spacr.wash";            // folder background: none|light|medium|strong

// Switchr. The badge in the Agents list reads WHERE: { [address]: account }
// for agents not on the main account, and nothing else, so an entry there is
// always a badge. What the move menu needs besides (the kind, and whether the
// agent is busy) is kept apart in AGENTS, the whole answer of `which`.
// The addresses are the core's terminal IDs ("t12"); Herdr's own agents list
// joins agents() and terminals() on the same key, so they match as they are.
const WHERE = "switchr.where";
const AGENTS = "switchr.agents";
const WHICH_EVERY = 3000;
const SWITCHR_LIST = "switchr-agents"; // the patched copy of Herdr's agents list
const NAME_RULE = /^[a-z0-9][a-z0-9-]{0,23}$/;
const KIND_NAMES = { claude: "Claude", codex: "Codex" };

// The modules this panel switches, in the order the column lists them.
function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

const ALL = [
	{ id: "browsr", name: "Browsr", icon: "mods/powr/browsr.svg",
		about: "A tab group for every space, shown as you switch spaces." },
	{ id: "spacr", name: "Spacr", icon: "mods/powr/spacr.svg",
		about: "Your projects in folders, each with its own colour and icon." },
	{ id: "switchr", name: "Switchr", icon: "mods/powr/switchr.svg",
		about: "A Claude or Codex account per agent, switched without logging out." },
];

/** What is left on an account, in one short line. */
function usageLine(account) {
  const u = account?.usage ?? {};
  if (account?.pending || u.state === "logged-out") return "Not logged in";
  if (u.state !== "ok" || u.session == null || u.week == null) return "limit unknown";
  return `5 h ${u.session}% · week ${u.week}%`;
}

/** The last line a failed command printed, without the program's name. */
function failure(result, fallback) {
  const last = String(result?.stderr ?? "").trim().split("\n").pop() || fallback;
  return last.replace(/^switchr:\s*/, "");
}

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// Which group you are in, said in colour.
//
// Chromium paints a tab group in one of nine named colours and takes nothing
// else — no hex — so the theme's accent cannot be handed to it. What it can
// be is matched: these are the nine as Chromium actually paints them, and
// the accent is answered with whichever is nearest. Change the Omarchy
// theme and the mark moves with it, because Herdr's accent moves with it.
const CHROME_COLORS = {
  grey: "#5f6368",
  blue: "#1a73e8",
  red: "#d93025",
  yellow: "#f9ab00",
  green: "#1e8e3e",
  pink: "#d01884",
  purple: "#9334e6",
  cyan: "#007b83",
  orange: "#fa903e",
};

// Where the answer is left for bridge.py, which sends it to the browser on
// every switch. The panel is the only part of Browsr that can see a theme,
// and the button is on screen all day, so it is the one that keeps it true.
const ACCENT = "browsr.accent";

// What the groups wear at rest. A mark in the same colour is no mark, so
// grey is never the answer however grey the accent is.
const RESTING = "grey";

const rgb = (hex) => {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(hex ?? "").trim());
  if (!match) return null;
  const n = parseInt(match[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

/** The name of the Chromium colour closest to this one, never grey. */
function nearestColor(hex) {
  const want = rgb(hex);
  if (!want) return null;
  let best = null;
  let near = Infinity;
  for (const [name, value] of Object.entries(CHROME_COLORS)) {
    if (name === RESTING) continue;
    const have = rgb(value);
    const d = (want[0] - have[0]) ** 2 + (want[1] - have[1]) ** 2 + (want[2] - have[2]) ** 2;
    if (d < near) {
      near = d;
      best = name;
    }
  }
  return best;
}

export default class Powr extends View {
  init(_props, cx) {
    this.ask = props()?.ask ?? null;
    if (this.ask) {
      // The question dialog: nothing else of the panel runs in it.
      cx.notify();
      return;
    }
    if (props()?.role === "account") {
      // Switchr's move menu, opened from a right press on an agent row.
      this.initAccountMenu(cx);
      return;
    }
    this.status = null;
    this.note = "";
    this.working = null;
    this.page = "browsr";
    if (this.isButton()) {
      this.answered = null; // the id of the last question dealt with
      cx.timer.every(ASK_EVERY, (cx) => {
        this.pickUpQuestion(cx);
        this.recordNewSpaces(cx);
      });
      this.recordNewSpaces(cx);
      // Which account each agent is on, for the badges. The button is on
      // screen all day and exists once, so it is the one that keeps it.
      this.asking = false;
      cx.timer.every(WHICH_EVERY, (cx) => this.publishWhere(cx));
      this.publishWhere(cx);
    } else {
      setShared(PANEL, { open: true, changed: Date.now() });
      this.accounts = null;  // list --json, while the Switchr page is open
      this.listing = false;
      this.editing = null;   // { mode: "rename" | "add", kind, name }
      this.removing = null;  // "kind/name" waiting for its second press
      this.field = InputState.new({ placeholder: "Name" });
      this.field.on("submit", (_event, cx) => this.commitField(cx));
      cx.timer.every(STATUS_EVERY, (cx) => {
        this.refresh(cx);
        this.loadAccounts(cx);
      });
      this.refresh(cx);
    }
  }

  async close() {
    if (!this.ask && !this.isButton() && props()?.role !== "account") {
      setShared(PANEL, { open: false, changed: Date.now() });
    }
  }

  isButton() {
    return props()?.role === "button";
  }

  /**
   * { browsr: bool, spacr: bool, switchr: bool }. Every module is on unless
   * set off, except Switchr, which is off until switched on: it needs its
   * copy of the agents list in the layout, and only switching it on puts it
   * there.
   */
  modules() {
    const stored = shared(MODULES);
    const set = stored && typeof stored === "object" ? stored : {};
    return Object.fromEntries(ALL.map((m) =>
      [m.id, m.id === "switchr" ? set[m.id] === true : set[m.id] !== false]));
  }

  /**
   * Look for a question Browsr has left, and put it on screen.
   *
   * Polled rather than watched: one small file once a second, where a folder
   * watch would wake on everything else Browsr writes in there.
   */
  pickUpQuestion(cx) {
    if (this.modules().browsr === false) return;
    cx.spawn(async (cx) => {
      let asked;
      try {
        const text = await readTextFile(ASK);
        asked = text ? JSON.parse(text) : null;
      } catch {
        return;
      }
      if (!asked || !asked.id || asked.id === this.answered) return;
      // Stale: Browsr gives up after twenty seconds, and a question
      // nobody is waiting on should not appear.
      if (asked.at && Date.now() / 1000 - asked.at > 25) return;
      this.answered = asked.id;
      const mode = shared(WHEN_ASKED) ?? "ask";
      if (mode === "on" || mode === "never") {
        this.reply(asked, mode === "on", cx);
        cx.notify();
        return;
      }
      openOverlay({ mod: "powr", kind: "dialog", width: 430, props: { ask: asked } });
      cx.notify();
    });
  }

  /** Write the answer back, and turn the project on when it is yes. */
  answer(yes, cx) {
    this.reply(this.ask, yes, cx);
    const overlay = props()?.overlay;
    if (overlay) closeOverlay(overlay);
  }

  reply(asked, yes, cx) {
    if (yes) {
      setShared(ON, { ...this.browsers(), [asked.space]: true });
    }
    cx.spawn(async () => {
      try {
        await writeTextFile(ANSWER, JSON.stringify({ id: asked.id, yes }), null);
      } catch {
        // Browsr gives up on its own; nothing here can do better.
      }
    });
  }

  /**
   * A space never seen before gets the browser or not, as the setting says.
   * Each one is written down the first time it shows up, on or off, so a
   * space someone turned off is never mistaken for a new one.
   */
  recordNewSpaces(cx) {
    const chosen = this.browsers();
    const fresh = spaces().filter((space) => space.id && !(space.id in chosen));
    if (!fresh.length) return;
    const on = shared(NEW_ON) === true;
    setShared(ON, { ...chosen, ...Object.fromEntries(fresh.map((space) => [space.id, on])) });
    if (on && this.modules().browsr !== false) {
      cx.spawn(async () => {
        try { await exec(BRIDGE, ["--app", "sync"]); } catch {}
      });
    }
  }

  renderAsk(t) {
    const asked = this.ask;
    const button = (id, label, filled, handler) =>
      h_flex()
        .id(id)
        .h(30).px(14).items_center().rounded(7)
        .cursor_pointer()
        .border_1()
        .border_color(filled ? t.accent : t.border)
        .when(filled, (el) => el.bg(t.accent))
        .text_color(filled ? t.background : t.foreground)
        .hover((s) => s.opacity(0.88))
        .on_click((_event, cx) => handler(cx))
        .child(label);
    return v_flex()
      .id("powr-ask")
      .size_full()
      .p(18)
      .gap(10)
      .bg(t.popover)
      .text_size(13)
      .child(div().text_color(t.foreground).child(`Give ${asked.name} a browser?`))
      .child(div().text_size(12).text_color(t.dim).child(
        `Something in ${asked.name} wants to open a page, but this project is `
        + `switched off, so it has no tabs of its own. Turning it on gives it a `
        + `group in the Browsr window.`))
      .when(Boolean(asked.url), (el) => el.child(
        div().text_size(11).text_color(t.dim).truncate().child(asked.url)))
      .child(
        h_flex().gap(8).pt(4).justify_end()
          .child(button("powr-ask-no", "Leave it off", false, (cx) => this.answer(false, cx)))
          .child(button("powr-ask-yes", "Turn it on", true, (cx) => this.answer(true, cx)))
      );
  }

  /**
   * Switch a module. The switch is stored first, so everything reading it
   * agrees at once; then whatever the module runs is told.
   */
  switchModule(id, on, cx) {
    if (id === "browsr") setShared(MODULES, { ...this.modules(), [id]: on });
    if (id === "browsr" && !on) {
      // Its own path, not act(): act() does nothing while another command
      // runs, and a switch that is stored but never carried out is a lie.
      cx.spawn(async (cx) => {
        try {
          await exec(BRIDGE, ["--app", "off"]);
        } catch (error) {
          notify({ title: "Browsr", body: String(error?.message ?? error), level: "error" });
        }
        this.refresh(cx);
      });
    }
    if (id === "spacr") {
      // The switch only sticks when the layout followed: otherwise the
      // list stays what it was and the toggle would lie.
      if (!this.swapLayout(on ? "spaces" : "spacr", on ? "spacr" : "spaces")) {
        this.cannotSwap("project list");
        cx.notify();
        return;
      }
      setShared(MODULES, { ...this.modules(), [id]: on });
    }
    if (id === "switchr") {
      cx.spawn(async (cx) => {
        if (on) {
          // The copy is made by the installer; without it the layout
          // would point at a list that is not there.
          let copy = null;
          try { copy = await readTextFile(`${appHome()}/mods/${SWITCHR_LIST}/main.js`); } catch {}
          if (!copy) {
            notify({
              title: "Powr",
              body: "Switchr's agents list is not installed. Run install.py, then switch again.",
              level: "warning",
            });
            cx.notify();
            return;
          }
        }
        if (!this.swapLayout(on ? "agents" : SWITCHR_LIST, on ? SWITCHR_LIST : "agents")) {
          this.cannotSwap("agents list");
          cx.notify();
          return;
        }
        setShared(MODULES, { ...this.modules(), [id]: on });
        if (on) {
          this.loadAccounts(cx);
        } else {
          setShared(WHERE, {});
          setShared(AGENTS, {});
        }
        cx.notify();
      });
    }
    cx.notify();
  }

  /** Point the layout's `from` block at `to`. True when something changed. */
  swapLayout(from, to) {
    try {
      const { tree, changed } = swapMod(layout(), from, to);
      if (changed > 0) {
        run("layout.set", { layout: tree });
        return true;
      }
    } catch {}
    return false;
  }

  cannotSwap(what) {
    notify({
      title: "Powr",
      body: `This layout has no ${what} Powr can swap. Clone it first (Settings), then switch again.`,
      level: "warning",
    });
  }

  /**
   * Ask Switchr which account each agent is on and leave it for the badges.
   * Written only when the answer changed: the store is shared, and a write
   * that says nothing new would wake every panel reading it. A tick is
   * skipped while the previous question is still out.
   */
  publishWhere(cx) {
    if (!this.modules().switchr || this.asking) return;
    this.asking = true;
    cx.spawn(async () => {
      try {
        const r = await exec(SWITCHR, ["which", "--json"], { timeout: 10000 });
        if (r.code !== 0) return;
        const found = JSON.parse(r.stdout);
        if (!found || typeof found !== "object") return;
        const where = {};
        for (const [address, row] of Object.entries(found)) {
          if (row && row.main === false && row.account) where[address] = row.account;
        }
        if (!same(shared(WHERE), where)) setShared(WHERE, where);
        if (!same(shared(AGENTS), found)) setShared(AGENTS, found);
      } catch {
        // The next tick tries again; a missing badge is the honest failure.
      } finally {
        this.asking = false;
      }
    });
  }

  /**
   * Was the panel on screen when this press began?
   *
   * The button lives outside the popup, so pressing it dismisses the popup
   * first and only then delivers the click — which would reopen what the
   * person just closed, and the panel would never shut. Two things can be
   * true by the time the click lands: the panel is still there (nothing
   * dismissed it), or it has only just gone. Both mean the press was a
   * "close", so neither may reopen it.
   */
  pressedWhileOpen() {
    const panel = shared(PANEL) ?? {};
    return panel.open === true || Date.now() - (panel.changed ?? 0) < SAME_PRESS;
  }

  /**
   * Leave the theme's accent where bridge.py can read it, as one of the nine
   * names Chromium knows.
   *
   * Done while drawing, because that is when a mod is told the theme has
   * changed, and written only when the answer is a different one: the store
   * is shared, and a write that says nothing new would wake every panel
   * reading it.
   */
  publishAccent() {
    const name = nearestColor(theme().accent);
    if (name && shared(ACCENT) !== name) setShared(ACCENT, name);
  }
  /** The projects that have a browser, as { [id]: true }. */
  browsers() {
    const chosen = shared(ON);
    return chosen && typeof chosen === "object" ? chosen : {};
  }
  /** Ask bridge.py what the browser and the strip of groups look like. */
  refresh(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(BRIDGE, ["--app", "status"]);
        this.status = JSON.parse(r.stdout);
        this.note = "";
      } catch (error) {
        this.status = null;
        this.note = String(error?.message ?? error);
      }
      cx.notify();
    });
  }

  /** Run one of bridge.py's own commands and report how it went. */
  act(cx, argument, label) {
    if (this.working) return;
    this.working = label;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        await exec(BRIDGE, ["--app", argument]);
        this.note = "";
      } catch (error) {
        this.note = String(error?.message ?? error);
        notify({ title: "Browsr", body: this.note, level: "error" });
      }
      this.working = null;
      this.refresh(cx);
    });
  }

  /**
   * The state in one line, in the person's words, and the one thing that
   * would put it right.
   *
   * Both come from here so that they can never disagree: no cheerful line
   * beside a repair button, and no fault reported with nothing to press.
   * `fix` is null when the line is not a complaint, or when the cure is
   * already standing in the row.
   */
  state() {
    const line = (words, fix = null) => ({ words, fix });
    if (this.note) return line(this.note);
    if (!this.status) return line("Checking…");
    if (!this.status.browser) return line("Browser closed. Click Show browser to open it.");
    if (!this.status.bridge) return line("Extension not responding. Click Restart to fix it.");
    // The browser outlives any one Herdr, and the part that follows your
    // spaces is a separate program. It can be gone while everything else
    // looks fine, and then nothing moves when you switch. Starting it again
    // is what brings the follower back, and that button is always there.
    if (this.status.following === false) return line("Not following your spaces. Click Restart to fix it.");
    // The commonest surprise by far: the project you are looking at is
    // turned off below, so switching to it moves nothing and the browser
    // keeps showing whatever came before. Silence looks exactly like a
    // fault, so say it. Nothing to repair — it is a choice, made in the
    // list underneath.
    const here = focused()?.space;
    if (here && this.browsers()[here] !== true) return line("Browser is off for this space.");
    const strip = this.status.strip ?? {};
    if (strip.windows && strip.windows !== 1) {
      return line(`${strip.windows} browser windows. Click Resync to fix it.`, "Resync");
    }
    // By name, never by how many. Counting said "one per project" while
    // four projects had no group at all and three strangers sat in the
    // strip making up the difference — so the panel reported everything
    // fine, the repair button stayed hidden, and switching to any of those
    // four moved nothing, with nothing on screen admitting why.
    const wanted = this.status.workspaces ?? [];
    const titles = (strip.groups ?? []).map((group) => group.title).filter(Boolean);
    const missing = wanted.filter((name) => !titles.includes(name));
    const extra = titles.filter((name) => !wanted.includes(name));
    if (missing.length) {
      return line(missing.length === 1
        ? `${missing[0]} has no tab group. Click Resync to fix it.`
        : `${missing.length} spaces have no tab group. Click Resync to fix it.`, "Resync");
    }
    if (extra.length) {
      return line(extra.length === 1
        ? `Leftover tab group: ${extra[0]}. Click Resync to fix it.`
        : `${extra.length} leftover tab groups. Click Resync to fix it.`, "Resync");
    }
    return line(`All good: ${titles.length} tab groups.`);
  }

  /** Just the line, for the header. */
  words() {
    return this.state().words;
  }

  colour(t) {
    if (this.note) return t.blocked;
    if (!this.status) return t.unknown;
    return this.status.browser && this.status.bridge ? t.done : t.idle;
  }

  renderButton() {
    const t = theme();
    this.publishAccent();
    // A line under Herdr's own Settings, drawn the way Herdr draws those.
    const narrow = (find(layout(), "sidebar")?.size?.px ?? Infinity) < 160;
    return v_flex().size_full().bg(t.chrome).px(8).pb(8).text_size(13)
      .child(h_flex()
        .id("powr-button")
        .h(28).px(8).gap(9).items_center().rounded(8)
        .text_color(t.muted)
        .hover((s) => s.bg(t.hover).text_color(t.foreground))
        .cursor_pointer()
        .on_mouse_down("left", () => { this.closing = this.pressedWhileOpen(); })
        .on_click(() => {
          const open = (shared(PANEL) ?? {}).open === true;
          const closing = this.closing;
          this.closing = false;
          // Still there: close it. Already gone with this same press: leave it.
          if (!open && closing) return;
          run("mod.open", { name: "powr" });
        })
        .child(svg("mods/powr/zap.svg").size(14))
        .when(!narrow, (el) => el.child("Powr Pack")));
  }

  /** A switch drawn by hand: gpui-base's Switch has no styling of its own. */
  toggle(t, id, on, handler) {
    return div().id(`powr-switch-${id}`)
      .w(28).h(16).rounded(8).flex_none()
      .cursor_pointer()
      .bg(on ? t.accent : t.border)
      .on_click(handler)
      .child(div().size(12).rounded(6).mt(2).ml(on ? 14 : 2).bg(t.background));
  }

  renderColumn(t) {
    const on = this.modules();
    const column = v_flex().w(180).flex_none().h_full().p(8).gap(2)
      .border_r_1().border_color(t.border);
    for (const m of ALL) {
      const picked = this.page === m.id;
      column.child(div().id(`powr-page-${m.id}`)
        .px(6).py(5).rounded(6).cursor_pointer()
        .bg(picked ? t.selected : t.popover)
        .text_color(picked ? t.foreground : t.dim)
        .opacity(on[m.id] ? 1 : 0.5)
        .hover((s) => s.bg(t.hover))
        .on_click((_event, cx) => {
          this.page = m.id;
          this.loadAccounts(cx);
          cx.notify();
        })
        .child(h_flex().items_center().gap(8)
          .child(svg(m.icon).size(14))
          .child(m.name)));
    }
    return column;
  }

  /** The page's own top line: its name, and the one switch for it. */
  renderHeader(t, m, on) {
    const colour = !on ? t.dim : m.id === "browsr" ? this.colour(t) : t.foreground;
    return h_flex().items_center().gap(10).pb(10).border_b_1().border_color(t.border)
      .child(svg(m.icon).size(18).flex_none().text_color(colour))
      .child(v_flex().flex_1().min_w(0).gap(2)
        .child(div().text_size(15).text_color(t.foreground).child(m.name))
        .child(div().text_size(12).text_color(t.dim).child(m.about)))
      .child(this.toggle(t, m.id, on, (_event, cx) => this.switchModule(m.id, !on, cx)));
  }

  renderBrowsrPage(t) {
    const { words, fix } = this.state();
    // One action per row: the button, and beside it what it does.
    const row = (label, argument, about) =>
      h_flex().items_center().gap(12)
        .child(div().id(`powr-${argument}`).w(110).flex_none().py(4).rounded(5)
          .flex().justify_center()
          .bg(t.surface)
          .text_color(this.working === label ? t.dim : t.foreground)
          .hover((s) => s.bg(t.hover))
          .on_click((_event, cx) => this.act(cx, argument, label))
          .child(this.working === label ? "…" : label))
        .child(div().text_size(12).text_color(t.dim).child(about));
    return v_flex().gap(8)
      .child(row("Show browser", "launch", "Jump to the browser window."))
      .child(row("Restart", "restart", "Close the browser and open it again."))
      .when(fix !== null, (el) => el.child(row(fix, "sync", "Match the tab groups to your spaces.")))
      .child(div().pt(6).text_size(12).text_color(t.dim).child(words))
      .child(this.section(t, "Settings"))
      .child(this.setting(t, "New spaces", "Turn the browser on for new spaces and worktrees.",
        this.toggle(t, "new-on", shared(NEW_ON) === true,
          (_event, cx) => { setShared(NEW_ON, shared(NEW_ON) !== true); cx.notify(); })))
      .child(this.setting(t, "Agent asks", "When a space without a browser needs one.",
        this.choice(t, "when-asked", shared(WHEN_ASKED) ?? "ask",
          [["ask", "Ask"], ["on", "Turn on"], ["never", "Refuse"]],
          (value) => setShared(WHEN_ASKED, value))))
      .child(this.setting(t, "Fold other groups", "Only the current space's tabs stay open.",
        this.toggle(t, "fold", shared(FOLD) !== false,
          (_event, cx) => { setShared(FOLD, shared(FOLD) === false); cx.notify(); })))
      .child(this.setting(t, "Herdr links", "Open links clicked in Herdr in Browsr.",
        this.toggle(t, "links", shared(LINKS) === true,
          (_event, cx) => this.switchLinks(shared(LINKS) !== true, cx))));
  }

  section(t, title) {
    return div().pt(14).pb(2).text_size(11).text_color(t.dim).child(title.toUpperCase());
  }

  /** One setting: its name and a short note on the left, its control on the right. */
  /**
   * Make Browsr the system's handler for web links, or give them back.
   * Stored only once the bridge has done it, so the switch never shows
   * a state the system is not in.
   */
  switchLinks(on, cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(BRIDGE, ["--app", "links", on ? "on" : "off"]);
        if (r.code !== 0) throw new Error(r.stderr.trim().split("\n").pop() || "Could not change links");
        setShared(LINKS, on);
      } catch (error) {
        notify({ title: "Browsr", body: String(error?.message ?? error), level: "error" });
      }
      cx.notify();
    });
  }

  setting(t, name, note, control) {
    return h_flex().items_center().gap(12).py(4)
      .child(v_flex().flex_1().min_w(0)
        .child(div().text_color(t.foreground).child(name))
        .child(div().text_size(12).text_color(t.dim).child(note)))
      .child(control);
  }

  /** A row of mutually exclusive buttons, as Herdr's Settings draws them. */
  choice(t, id, value, options, pick) {
    const row = h_flex().flex_none().p(2).gap(2).rounded(7).bg(t.surface);
    for (const [key, label] of options) {
      const on = key === value;
      row.child(div().id(`powr-${id}-${key}`).px(10).py(3).rounded(5).cursor_pointer()
        .when(on, (el) => el.bg(t.selected))
        .text_color(on ? t.foreground : t.dim)
        .hover((s) => s.text_color(t.foreground))
        .on_click((_event, cx) => { pick(key); cx.notify(); })
        .child(label));
    }
    return row;
  }

  renderSpacrPage(t) {
    const line = (text) => div().text_size(12).text_color(t.dim).child(text);
    return v_flex().gap(8)
      .child(line("Left press: select a project, fold or unfold a folder."))
      .child(line("Right press on a project's icon: its colour and glyph."))
      .child(line("Right press on the rest of a project's row: Herdr's own menu."))
      .child(line("Right press on a folder: its name, colour, glyph and Delete."))
      .child(line("Browser mark on a row: browser on or off for that space."))
      .child(line("Drag a project onto a folder to put it there, below the list to take it out."))
      .child(this.section(t, "Settings"))
      .child(this.setting(t, "Folder background", "How strongly a folder's colour shows behind it.",
        this.choice(t, "wash", shared(WASH) ?? "medium",
          [["none", "None"], ["light", "Light"], ["medium", "Medium"], ["strong", "Strong"]],
          (value) => setShared(WASH, value))));
  }

  /** Switchr's accounts and what is left on each, while its page is open. */
  loadAccounts(cx) {
    if (this.page !== "switchr" || !this.modules().switchr || this.listing) return;
    this.listing = true;
    cx.spawn(async (cx) => {
      try {
        const r = await exec(SWITCHR, ["list", "--json"], { timeout: 20000 });
        if (r.code !== 0) throw new Error(failure(r, "Switchr could not list the accounts."));
        const rows = JSON.parse(r.stdout);
        this.accounts = Array.isArray(rows) ? rows : [];
        this.accountsNote = "";
      } catch (error) {
        this.accountsNote = String(error?.message ?? error);
      } finally {
        this.listing = false;
      }
      cx.notify();
    });
  }

  /** Put the one name field on a row: an account being renamed, or a new one. */
  startEditing(edit, cx) {
    this.editing = edit;
    this.removing = null;
    this.fieldNote = "";
    this.field.set_value(edit.mode === "rename" ? edit.name : "");
    this.field.focus();
    cx.notify();
  }

  commitField(cx) {
    const edit = this.editing;
    if (!edit) return;
    const typed = this.field.value().trim();
    if (edit.mode === "rename" && typed === edit.name) {
      this.editing = null;
      cx.notify();
      return;
    }
    if (!NAME_RULE.test(typed)) {
      this.fieldNote = "Use small letters, digits and dashes, up to 24 characters.";
      cx.notify();
      return;
    }
    if ((this.accounts ?? []).some((a) => a.kind === edit.kind && a.name === typed)) {
      this.fieldNote = `There is already a ${KIND_NAMES[edit.kind]} account called ${typed}.`;
      cx.notify();
      return;
    }
    this.editing = null;
    if (edit.mode === "add") {
      // Logging in happens in the agent's own sign-in, so it gets a tab to
      // talk in; the panel steps aside so that tab is what you see.
      run("tab.new", { name: `Log in: ${typed}`, argv: [SWITCHR, "login", edit.kind, typed] });
      const overlay = props()?.overlay;
      if (overlay) closeOverlay(overlay);
      return;
    }
    this.switchrCommand(["rename", edit.kind, edit.name, typed], "Could not rename it.", cx);
  }

  /** Remove asks twice: the first press only turns the button into "Sure?". */
  removeAccount(account, cx) {
    const key = `${account.kind}/${account.name}`;
    if (this.removing !== key) {
      this.removing = key;
      this.editing = null;
      cx.notify();
      return;
    }
    this.removing = null;
    this.switchrCommand(["remove", account.kind, account.name], "Could not remove it.", cx);
  }

  switchrCommand(args, fallback, cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(SWITCHR, args, { timeout: 20000 });
        if (r.code !== 0) throw new Error(failure(r, fallback));
      } catch (error) {
        notify({ title: "Switchr", body: String(error?.message ?? error), level: "error" });
      }
      this.loadAccounts(cx);
      cx.notify();
    });
  }

  smallButton(t, id, label, handler) {
    return div().id(id).flex_none().px(10).py(3).rounded(5).cursor_pointer()
      .bg(t.surface).text_color(t.foreground)
      .hover((s) => s.bg(t.hover))
      .on_click((_event, cx) => handler(cx))
      .child(label);
  }

  /** The name field, drawn the way Spacr draws its folder name. */
  nameField(t) {
    return v_flex().flex_1().min_w(0).gap(3)
      .child(div().h(24).px(6).rounded(5).border_1().border_color(t.accent).cursor_text()
        .child(Input.new(this.field).h_full().text_size(13).text_color(t.foreground)))
      .child(div().text_size(11).text_color(this.fieldNote ? t.blocked : t.dim)
        .child(this.fieldNote || (this.editing?.mode === "add"
          ? "Type a name and press Enter. A tab opens for you to log in."
          : "Type the new name and press Enter.")));
  }

  accountRow(t, account) {
    const key = `${account.kind}/${account.name}`;
    const renaming = this.editing?.mode === "rename" && this.editing.kind === account.kind
      && this.editing.name === account.name;
    const row = h_flex().items_center().gap(8).py(3);
    if (renaming) {
      row.child(this.nameField(t));
    } else {
      row.child(v_flex().flex_1().min_w(0)
        .child(h_flex().items_center().gap(6)
          .child(div().text_color(t.foreground).truncate().child(account.name))
          .when(account.main === true, (el) => el.child(
            div().text_size(11).text_color(t.dim).child("main"))))
        .child(div().text_size(12).text_color(t.dim).child(usageLine(account))));
    }
    row.child(this.smallButton(t, `switchr-rename-${key}`, "Rename",
      (cx) => this.startEditing({ mode: "rename", kind: account.kind, name: account.name }, cx)));
    if (account.main !== true) {
      row.child(this.smallButton(t, `switchr-remove-${key}`,
        this.removing === key ? "Sure?" : "Remove", (cx) => this.removeAccount(account, cx)));
    }
    return row;
  }

  renderSwitchrPage(t) {
    const line = (text) => div().text_size(12).text_color(t.dim).child(text);
    const page = v_flex().gap(4)
      .child(line("Right press on an agent in the Agents list to move it to another account."))
      .child(line("An agent on an account other than main shows that account's name."));
    if (this.accounts === null) {
      return page.child(div().pt(10).text_size(12).text_color(t.dim)
        .child(this.accountsNote || "Checking…"));
    }
    if (this.accountsNote) page.child(div().pt(6).text_size(12).text_color(t.blocked).child(this.accountsNote));
    for (const kind of Object.keys(KIND_NAMES)) {
      page.child(this.section(t, `${KIND_NAMES[kind]} accounts`));
      for (const account of this.accounts.filter((a) => a.kind === kind)) {
        page.child(this.accountRow(t, account));
      }
      if (this.editing?.mode === "add" && this.editing.kind === kind) {
        page.child(h_flex().py(3).child(this.nameField(t)));
      } else {
        page.child(h_flex().py(3).child(this.smallButton(t, `switchr-add-${kind}`,
          `Add ${KIND_NAMES[kind]} account`, (cx) => this.startEditing({ mode: "add", kind }, cx))));
      }
    }
    return page;
  }

  /**
   * The move menu. What `which` said last is on screen at once; a fresh
   * answer, with the accounts and their limits, replaces it a moment later,
   * so "busy" is never a few seconds stale when it matters.
   */
  initAccountMenu(cx) {
    this.address = props()?.address ?? null;
    const known = shared(AGENTS);
    this.agent = known && typeof known === "object" ? known[this.address] ?? null : null;
    this.menuAccounts = null;
    this.menuNote = "";
    this.moving = null;
    cx.spawn(async (cx) => {
      try {
        const [which, list] = await Promise.all([
          exec(SWITCHR, ["which", "--json"], { timeout: 10000 }),
          exec(SWITCHR, ["list", "--json"], { timeout: 20000 }),
        ]);
        if (which.code !== 0) throw new Error(failure(which, "Switchr could not see the agents."));
        if (list.code !== 0) throw new Error(failure(list, "Switchr could not list the accounts."));
        this.agent = JSON.parse(which.stdout)?.[this.address] ?? null;
        this.menuAccounts = JSON.parse(list.stdout);
      } catch (error) {
        this.menuNote = String(error?.message ?? error);
      }
      cx.notify();
    });
    cx.notify();
  }

  moveTo(name, cx) {
    this.moving = name;
    cx.notify();
    cx.spawn(async () => {
      let ok = false;
      let message = "";
      try {
        const r = await exec(SWITCHR, ["move", this.address, name, "--json"], { timeout: 90000 });
        let out = null;
        try { out = JSON.parse(r.stdout); } catch {}
        ok = out?.ok === true;
        message = out ? String(out.message ?? "") : failure(r, "The move failed.");
      } catch (error) {
        message = String(error?.message ?? error);
      }
      notify({ title: "Switchr", body: message || (ok ? `Moved to ${name}.` : "The move failed."),
        level: ok ? "success" : "error" });
      const overlay = props()?.overlay;
      if (overlay) closeOverlay(overlay);
    });
  }

  renderAccountMenu(t) {
    const menu = v_flex().id("switchr-menu").size_full().p(6).gap(1).bg(t.popover).text_size(13);
    const note = (text, colour = t.dim) => div().px(6).py(4).text_size(12).text_color(colour).child(text);
    const agent = this.agent;
    if (this.menuNote) return menu.child(note(this.menuNote, t.blocked));
    if (!agent) {
      return menu.child(note(this.menuAccounts === null ? "Checking…"
        : "Switchr cannot move this agent. It moves Claude Code and Codex agents on this computer."));
    }
    menu.child(div().px(6).pt(2).pb(4).text_size(11).text_color(t.dim)
      .child(`${(KIND_NAMES[agent.kind] ?? agent.kind).toUpperCase()} ACCOUNT`));
    if (this.menuAccounts === null) return menu.child(note("Checking…"));
    const busy = !["idle", "done"].includes(agent.state);
    for (const account of this.menuAccounts.filter((a) => a.kind === agent.kind)) {
      const current = account.name === agent.account;
      const loggedIn = !account.pending && account.usage?.state !== "logged-out";
      const usable = !busy && !current && loggedIn && !this.moving;
      menu.child(h_flex().id(`switchr-to-${account.name}`)
        .items_center().gap(8).px(6).py(4).rounded(6)
        .opacity(current || usable || (this.moving === account.name) ? 1 : 0.5)
        .when(usable, (el) => el.cursor_pointer()
          .hover((s) => s.bg(t.hover))
          .on_click((_event, cx) => this.moveTo(account.name, cx)))
        .child(div().w(12).flex_none().text_color(t.accent).child(current ? "✓" : ""))
        .child(v_flex().flex_1().min_w(0)
          .child(div().truncate().text_color(t.foreground)
            .child(this.moving === account.name ? `Moving to ${account.name}…` : account.name))
          .child(div().text_size(11).text_color(t.dim).child(usageLine(account)))));
    }
    if (busy) menu.child(note("Busy — you can move it once it has finished."));
    return menu;
  }

  render() {
    if (this.ask) return this.renderAsk(theme());
    if (props()?.role === "account") return this.renderAccountMenu(theme());
    if (this.isButton()) return this.renderButton();
    const t = theme();
    this.publishAccent();
    const on = this.modules();
    const m = ALL.find((m) => m.id === this.page) ?? ALL[0];
    const body = v_flex().flex_1().min_w(0).h_full().p(16).gap(12)
      .child(this.renderHeader(t, m, on[m.id]))
      .when(Boolean(on[m.id]), (el) => el.child(m.id === "browsr" ? this.renderBrowsrPage(t)
        : m.id === "spacr" ? this.renderSpacrPage(t)
        : this.renderSwitchrPage(t)));
    return h_flex()
      .id("root")
      .size_full()
      .bg(t.popover)
      .text_size(13)
      .on_mouse_down_out(() => {
        const overlay = props()?.overlay;
        if (overlay) closeOverlay(overlay);
      })
      .child(this.renderColumn(t))
      .child(body);
  }
}
