import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { exec, spaces, focused, theme, home, notify, run, props, shared, setShared, closeOverlay } from "herdr";

// Browsr keeps one browser tab group per space, named after it, and brings
// that group forward when you switch spaces.
//
// Following the space is not this mod's job: Browsr's own watcher does it,
// started with the browser, reading the app's view.json. A mod is alive only
// while it is on screen, and the browser has to keep up whether or not
// anyone is looking at a panel.
//
// Two roles, one mod, chosen by props:
//   { "role": "button" }  a square in the chrome that opens the panel
//   anything else         the panel: state, actions, and which spaces get a
//                         browser group at all

// Every call says --app. `exec` hands the person's own environment to the
// program, with none of the variables a terminal inside Herdr carries, so
// nothing in it tells bridge.py which Herdr is asking. Without this it can
// read the other one's spaces and prune this one's groups against them.
//
// Where Browsr is checked out. Change this line if yours is elsewhere.
const BRIDGE = `${home()}/Projects/browsr/bridge.py`;

// Which projects have a browser: { [spaceId]: true }. A project is in here
// only if someone said so — a new project, and a new worktree, starts
// without one, because most never need a browser and the first page an
// agent wants to open is what asks.
//
// bridge.py reads it straight out of the app's shared state, so the watcher
// and the panel cannot come to disagree about it.
const ON = "browsr.on";
// How it used to be: everything had one unless it was turned off. Read once,
// to carry the choices already made over to the list the other way round.
const SKIP = "browsr.skip";

// How often to ask the browser how it is doing, while the panel is open.
const STATUS_EVERY = 4000;

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

// Whether the panel is on screen, and when that last changed:
// { open: boolean, changed: <ms> }. The button needs it to answer one
// question — see `pressedWhileOpen` below.
const PANEL = "browsr.panel";

// A press beside a popup dismisses it before the click arrives, and the
// panel's own teardown may land either side of that click. Anything that
// happened this recently is the same press.
const SAME_PRESS = 250;

export default class Browsr extends View {
  init(_props, cx) {
    this.status = null;
    this.note = "";
    this.working = null;
    if (!this.isButton()) {
      this.seedBrowsers();
      setShared(PANEL, { open: true, changed: Date.now() });
      cx.timer.every(STATUS_EVERY, (cx) => this.refresh(cx));
      this.refresh(cx);
    }
  }

  async close() {
    if (!this.isButton()) setShared(PANEL, { open: false, changed: Date.now() });
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

  isButton() {
    return props()?.role === "button";
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

  /**
   * Carry the old list over, once. It said which projects were turned
   * *off*, so reading it as the new one would take the browser away from
   * everything at the same moment.
   */
  seedBrowsers() {
    if (shared(ON)) return;
    const off = shared(SKIP);
    const map = {};
    for (const space of spaces()) {
      if (!(off && typeof off === "object" && off[space.id])) map[space.id] = true;
    }
    setShared(ON, map);
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

  /** Turn Browsr on or off for one space, and let the strip follow at once. */
  toggle(cx, space) {
    const chosen = { ...this.browsers() };
    if (chosen[space]) delete chosen[space];
    else chosen[space] = true;
    setShared(ON, chosen);
    this.act(cx, "sync", "match");
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
    const groups = (strip.groups ?? []).length;
    const on = this.browsers();
    const wanted = spaces().filter((space) => on[space.id] === true).length;
    if (groups !== wanted) return line(`${groups} groups, ${wanted} spaces want one`, "resync");
    return line(`${groups} groups, one per space`);
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
      .id("browsr-button")
      .size_full()
      .items_center()
      .justify_center()
      .bg(t.chrome)
      .tooltip("Browsr")
      .hover((s) => s.bg(t.hover))
      .on_mouse_down("left", () => { this.closing = this.pressedWhileOpen(); })
      .on_click(() => {
        const open = (shared(PANEL) ?? {}).open === true;
        const closing = this.closing;
        this.closing = false;
        // Still there: close it. Already gone with this same press: leave it.
        if (!open && closing) return;
        run("mod.open", { name: "browsr" });
      })
      // One glyph, tinted by how the browser is: green when it is up and
      // answering, grey when it is not running, amber when something is
      // wrong. A separate status dot beside it would say the same thing
      // twice in a button 38px wide.
      .child(svg("mods/browsr/browsr.svg").size(16).text_color(this.colour(t)));
  }

  render(cx) {
    if (this.isButton()) return this.renderButton();
    const t = theme();
    this.publishAccent();
    const on = this.browsers();
    const { words, fix } = this.state();

    // Each label names what it acts on, and says what will happen rather
    // than what the machinery is called. "match" on its own reads as a
    // setting, not as an action with a consequence.
    const action = (label, argument, hint) =>
      div()
        .id(`browsr-${argument}`)
        .px(9)
        .py(3)
        .rounded(5)
        .bg(t.surface)
        .text_color(this.working === label ? t.dim : t.foreground)
        .hover((s) => s.bg(t.hover))
        .tooltip(hint)
        .on_click((_event, cx) => this.act(cx, argument, label))
        .child(this.working === label ? "…" : label);

    const row = (space) => {
      const has = on[space.id] === true;
      return h_flex()
        .id(`browsr-space-${space.id}`)
        .items_center()
        .gap(9)
        .px(8)
        .py(3)
        .rounded(5)
        .hover((s) => s.bg(t.hover))
        .on_click((_event, cx) => this.toggle(cx, space.id))
        // A tick, or the room one would take. A drawn box with a glyph
        // inside came out as a smudge at this size and took its colour from
        // whatever showed through the panel; this is how the app marks a
        // chosen row everywhere else.
        .child(
          h_flex()
            .size(15)
            .flex_none()
            .items_center()
            .justify_center()
            .when(has, (el) =>
              el.child(svg("mods/browsr/check.svg").size(13).text_color(t.accent)))
        )
        .child(div().flex_1().text_color(has ? t.foreground : t.dim).child(space.name ?? space.id))
        .child(div().text_color(t.dim).text_size(11).child(has ? "" : "no browser"));
    };

    return v_flex()
      .id("root")
      .size_full()
      .bg(t.popover)
      // A press anywhere else puts the panel away, which is what a panel
      // that drops out of a button should do. Said here rather than left to
      // the window, so that it is the same however the panel was opened.
      .on_mouse_down_out(() => {
        const overlay = props()?.overlay;
        if (overlay) closeOverlay(overlay);
      })
      .p(12)
      .gap(10)
      .text_size(13)
      .child(
        h_flex()
          .items_center()
          .gap(8)
          .child(div().size(8).rounded(4).bg(this.colour(t)))
          .child(div().text_color(t.foreground).child("Browsr"))
          .child(div().flex_1().text_color(t.dim).child(words))
      )
      .child(
        // Two things you do to the browser itself, and nothing else. Every
        // other command this panel once offered was either a repair for a
        // state the line above now names, or a way to undo a decision the
        // list below makes one project at a time.
        h_flex()
          .gap(6)
          .child(action("show browser", "launch",
            "Bring the browser window forward. The only thing that starts it."))
          .child(action("restart", "restart",
            "Close the browser and start it again. Brings back whatever stopped "
            + "following, and the only way it reads a changed extension."))
          // Only when something is actually wrong, saying what it will do
          // about the line above. Standing there in the quiet, it would be
          // one more thing to wonder about.
          .when(fix !== null, (el) => el.child(action(fix, "sync",
            "Make the groups match the list below: add the missing ones, "
            + "take away the ones nothing wants.")))
      )
      .child(div().text_color(t.dim).text_size(11).child("A browser group for:"))
      .child(v_flex().gap(1).children(spaces().map(row)));
  }
}
