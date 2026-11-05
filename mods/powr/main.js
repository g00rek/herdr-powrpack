import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { exec, focused, theme, home, notify, run, props, shared, setShared,
	closeOverlay, layout, openOverlay, readTextFile, writeTextFile } from "herdr";
import { swapMod } from "./layout-swap.js";

// Powr: one button in the chrome and one panel. The panel switches the
// modules of the pack on and off and shows a page for each.
//   { "role": "button" }  a square in the chrome that opens the panel
//   anything else         the panel

// Where Browsr is checked out. Change this line if yours is elsewhere.
const BRIDGE = `${home()}/Projects/browsr/bridge.py`;
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
];

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
    this.status = null;
    this.note = "";
    this.working = null;
    this.page = "browsr";
    if (this.isButton()) {
      this.answered = null; // the id of the last question dealt with
      cx.timer.every(ASK_EVERY, (cx) => this.pickUpQuestion(cx));
    } else {
      setShared(PANEL, { open: true, changed: Date.now() });
      cx.timer.every(STATUS_EVERY, (cx) => this.refresh(cx));
      this.refresh(cx);
    }
  }

  async close() {
    if (!this.ask && !this.isButton()) setShared(PANEL, { open: false, changed: Date.now() });
  }

  isButton() {
    return props()?.role === "button";
  }

  /** { browsr: bool, spacr: bool }, every module on unless set off. */
  modules() {
    const stored = shared(MODULES);
    const set = stored && typeof stored === "object" ? stored : {};
    return Object.fromEntries(ALL.map((m) => [m.id, set[m.id] !== false]));
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
      openOverlay({ mod: "powr", kind: "dialog", width: 430, props: { ask: asked } });
      cx.notify();
    });
  }

  /** Write the answer back, and turn the project on when it is yes. */
  answer(yes, cx) {
    const asked = this.ask;
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
    const overlay = props()?.overlay;
    if (overlay) closeOverlay(overlay);
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
      let done = false;
      try {
        const { tree, changed } = on
          ? swapMod(layout(), "spaces", "spacr")
          : swapMod(layout(), "spacr", "spaces");
        if (changed > 0) {
          run("layout.set", { layout: tree });
          done = true;
        }
      } catch {}
      if (!done) {
        notify({
          title: "Powr",
          body: "This layout has no project list Powr can swap. Clone it first (Settings), then switch again.",
          level: "warning",
        });
        cx.notify();
        return;
      }
      setShared(MODULES, { ...this.modules(), [id]: on });
    }
    cx.notify();
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
        .on_click((_event, cx) => { this.page = m.id; cx.notify(); })
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
      .child(div().pt(6).text_size(12).text_color(t.dim).child(words));
  }

  renderSpacrPage(t) {
    const line = (text) => div().text_size(12).text_color(t.dim).child(text);
    return v_flex().gap(8)
      .child(line("Left press: select a project, fold or unfold a folder."))
      .child(line("Right press on a project's icon: its colour and glyph."))
      .child(line("Right press on the rest of a project's row: Herdr's own menu."))
      .child(line("Right press on a folder: its name, colour, glyph and Delete."))
      .child(line("Browser mark on a row: browser on or off for that space."))
      .child(line("Drag a project onto a folder to put it there, below the list to take it out."));
  }

  render() {
    if (this.ask) return this.renderAsk(theme());
    if (this.isButton()) return this.renderButton();
    const t = theme();
    this.publishAccent();
    const on = this.modules();
    const m = ALL.find((m) => m.id === this.page) ?? ALL[0];
    const body = v_flex().flex_1().min_w(0).h_full().p(16).gap(12)
      .child(this.renderHeader(t, m, on[m.id]))
      .when(Boolean(on[m.id]), (el) => el.child(m.id === "browsr" ? this.renderBrowsrPage(t)
        : this.renderSpacrPage(t)));
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
