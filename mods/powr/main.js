import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { exec, focused, theme, home, notify, run, props, shared, setShared,
	closeOverlay, layout } from "herdr";
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

// Which projects have a browser: { [spaceId]: true }. Read by bridge.py too.
const ON = "browsr.on";

// The modules this panel switches, in the order the column lists them.
const ALL = [
	{ id: "browsr", name: "Browsr" },
	{ id: "spacr", name: "Spacr" },
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
    this.status = null;
    this.note = "";
    this.working = null;
    this.page = "browsr";
    if (!this.isButton()) {
      setShared(PANEL, { open: true, changed: Date.now() });
      cx.timer.every(STATUS_EVERY, (cx) => this.refresh(cx));
      this.refresh(cx);
    }
  }

  async close() {
    if (!this.isButton()) setShared(PANEL, { open: false, changed: Date.now() });
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
   * Switch a module. The switch is stored first, so everything reading it
   * agrees at once; then whatever the module runs is told.
   */
  switchModule(id, on, cx) {
    setShared(MODULES, { ...this.modules(), [id]: on });
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
      const { tree, changed } = on
        ? swapMod(layout(), "spaces", "spacr")
        : swapMod(layout(), "spacr", "spaces");
      if (changed > 0) run("layout.set", { layout: tree });
    }
    if (!on && this.page === id) this.page = null;
    if (on) this.page = id;
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
    if (!this.status) return line("asking…");
    if (!this.status.browser) return line("not running");
    if (!this.status.bridge) return line("running, but its extension is not answering");
    // The browser outlives any one Herdr, and the part that follows your
    // spaces is a separate program. It can be gone while everything else
    // looks fine, and then nothing moves when you switch. Starting it again
    // is what brings the follower back, and that button is always there.
    if (this.status.following === false) return line("running, but nothing is following your spaces");
    // The commonest surprise by far: the project you are looking at is
    // turned off below, so switching to it moves nothing and the browser
    // keeps showing whatever came before. Silence looks exactly like a
    // fault, so say it. Nothing to repair — it is a choice, made in the
    // list underneath.
    const here = focused()?.space;
    if (here && this.browsers()[here] !== true) return line("this project has no browser — nothing follows here");
    const strip = this.status.strip ?? {};
    if (strip.windows && strip.windows !== 1) {
      return line(`${strip.windows} windows — there should be one`, "resync");
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
        ? `${missing[0]} has no group`
        : `${missing.length} projects have no group`, "resync");
    }
    if (extra.length) {
      return line(extra.length === 1
        ? `a group nothing wants: ${extra[0]}`
        : `${extra.length} groups nothing wants`, "resync");
    }
    return line(`${titles.length} groups, one per project`);
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
    return h_flex()
      .id("powr-button")
      .size_full()
      .items_center()
      .justify_center()
      .bg(t.chrome)
      .tooltip("Powr")
      .hover((s) => s.bg(t.hover))
      .on_mouse_down("left", () => { this.closing = this.pressedWhileOpen(); })
      .on_click(() => {
        const open = (shared(PANEL) ?? {}).open === true;
        const closing = this.closing;
        this.closing = false;
        // Still there: close it. Already gone with this same press: leave it.
        if (!open && closing) return;
        run("mod.open", { name: "powr" });
      })
      .child(svg("mods/powr/powr.svg").size(16).text_color(t.foreground));
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
      .border_r_1().border_color(t.border)
      .child(div().px(6).pb(4).text_size(11).text_color(t.dim).child("Modules"));
    for (const m of ALL) {
      column.child(h_flex().px(6).py(4).items_center().gap(8)
        .child(div().flex_1().text_color(on[m.id] ? t.foreground : t.dim).child(m.name))
        .child(this.toggle(t, m.id, on[m.id],
          (_event, cx) => this.switchModule(m.id, !on[m.id], cx))));
    }
    column.child(div().h(1).my(6).bg(t.border));
    for (const m of ALL.filter((m) => on[m.id])) {
      const picked = this.page === m.id;
      column.child(div().id(`powr-page-${m.id}`)
        .px(6).py(5).rounded(6).cursor_pointer()
        .bg(picked ? t.selected : t.popover)
        .text_color(picked ? t.foreground : t.dim)
        .hover((s) => s.bg(t.hover))
        .on_click((_event, cx) => { this.page = m.id; cx.notify(); })
        .child(m.name));
    }
    return column;
  }

  renderBrowsrPage(t) {
    const { words, fix } = this.state();
    const action = (label, argument, hint) =>
      div().id(`powr-${argument}`).px(9).py(3).rounded(5)
        .bg(t.surface)
        .text_color(this.working === label ? t.dim : t.foreground)
        .hover((s) => s.bg(t.hover))
        .tooltip(hint)
        .on_click((_event, cx) => this.act(cx, argument, label))
        .child(this.working === label ? "…" : label);
    return v_flex().gap(10)
      .child(h_flex().items_center().gap(8)
        .child(svg("mods/powr/browsr.svg").size(16).text_color(this.colour(t)))
        .child(div().text_color(t.foreground).child("Browsr"))
        .child(div().flex_1().text_color(t.dim).child(words)))
      .child(h_flex().gap(6)
        .child(action("show browser", "launch",
          "Bring the browser window forward. The only thing that starts it."))
        .child(action("restart", "restart",
          "Close the browser and start it again, with everything that runs beside it."))
        .when(fix !== null, (el) => el.child(action(fix, "sync",
          "Make the groups match the projects that have a browser."))))
      .child(div().text_size(11).text_color(t.dim)
        .child("Which projects have a browser is set on the project list: the browser mark on each row."));
  }

  renderSpacrPage(t) {
    const line = (text) => div().text_size(12).text_color(t.dim).child(text);
    return v_flex().gap(8)
      .child(div().text_color(t.foreground).child("Spacr"))
      .child(line("Left press: select a project, fold or unfold a folder."))
      .child(line("Right press on a project's icon: its colour and glyph."))
      .child(line("Right press on the rest of a project's row: Herdr's own menu."))
      .child(line("Right press on a folder: its name, colour, glyph and Delete."))
      .child(line("Drag a project onto a folder to put it there, below the list to take it out."));
  }

  render() {
    if (this.isButton()) return this.renderButton();
    const t = theme();
    this.publishAccent();
    const on = this.modules();
    const page = this.page && on[this.page] ? this.page : null;
    const body = v_flex().flex_1().min_w(0).h_full().p(16).gap(10)
      .child(page === "browsr" ? this.renderBrowsrPage(t)
        : page === "spacr" ? this.renderSpacrPage(t)
        : div().text_color(t.dim).child("Switch a module on to see its page."));
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
