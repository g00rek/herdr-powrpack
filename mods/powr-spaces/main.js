import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex, InputState, Input } from "gpui-base";
import { spaces, panes, focused, theme, run, shared, setShared, openOverlay, closeOverlay,
	exec, props, readTextFile, writeTextFile, home } from "herdr";

// The list of projects, drawn by us rather than by Herdr, so that things
// Herdr knows nothing about can appear on a project's own row. The first of
// those is Browsr: every project here says whether it has a browser, and the
// mark is the switch.
//
// Herdr's own list cannot carry that. A panel draws all of its own content
// and there is no way to hand it something from outside, so a mark on a
// project row means owning the list. This is that list.

// A project's colour, as Herdr stores it, and what each name looks like.
// The same six colours the rest of the app paints avatars with, so a project
// does not change colour depending on which panel you are looking at.
const HERDR_COLORS = "space.colors";
const MARK = {
	ansi1: "#d9649a",
	ansi2: "#3fae6a",
	ansi3: "#e3814f",
	ansi4: "#5b8def",
	ansi5: "#8b7cf6",
	ansi6: "#22a99a",
};
const HUES = Object.keys(MARK);

// Our own folders, kept apart from the ones Herdr's list uses. Seeded from
// those once, so the folders you already have are here on the first run, and
// never written back: nothing we do can damage the list you can switch to.
// What a folder can wear. Every one of them is a folder — the shape says
// "this holds projects" and nothing else should have to. They differ only in
// the mark on the flap, which is the part that says which folder it is.
// Lucide, shipped in this mod's own icons folder with the ISC licence.
const ICONS = [
	"folder", "folder-code", "folder-git", "folder-git-2", "folder-kanban",
	"folder-tree", "folder-archive", "folder-bookmark", "folder-check",
	"folder-clock", "folder-cog", "folder-heart", "folder-key",
	"folder-lock", "folder-pen", "folder-search", "folder-user",
	"folder-users",
];

// What a project can wear instead of its initial. A project is anything, so
// these are not all one shape the way the folder glyphs are.
const SPACE_ICONS = [
	"code", "braces", "terminal", "rocket", "bug", "database", "server",
	"shield", "globe", "cloud", "cpu", "smartphone", "monitor", "gamepad-2",
	"camera", "film", "palette", "pen-tool", "feather", "book",
	"graduation-cap", "banknote", "chart-column", "briefcase", "mail",
	"phone", "users", "heart", "flask-conical", "brain", "microscope",
	"leaf", "flame", "zap", "wrench", "package", "bot", "map-pin", "calendar",
];

// Ours, because Herdr has nowhere to put it. Colours are Herdr's own, so
// that a project is the same colour in every panel that draws one.
const SPACE_ICON_OF = "powr.space-icons";

const FOLDERS = "powr.folders";
const HERDR_FOLDERS = "space-folders";

// Which projects have a browser: Browsr's own setting, shared with its panel
// so the two can never disagree. A project is in here only if someone said
// so — a new project, and a new worktree, starts without one.
const ON = "browsr.on";
// How it used to be: everything had one unless it was turned off. Read once,
// to carry the choices already made over to the list the other way round.
const SKIP = "browsr.skip";

// Where Browsr leaves a question and reads the answer. A file, because the
// two are different programs: one is a mod in this window, the other is a
// script an agent runs in a terminal somewhere else.
// Browsr's own command, for the one thing this list changes that lives
// outside Herdr. Change this line if your checkout is elsewhere.
const BRIDGE = `${home()}/Projects/browsr/bridge.py`;
const BROWSR_STATE = `${home()}/.local/state/herdr-dev-browser`;
const ASK = `${BROWSR_STATE}/ask.json`;
const ANSWER = `${BROWSR_STATE}/answer.json`;
const ASK_EVERY = 1000;

// Branches worth printing under a name. Almost every project sits on its
// trunk, so printing it on every row costs a second line each and tells you
// nothing; a branch is news only when it is not the trunk.
const TRUNK = new Set(["main", "master", "trunk", "develop"]);

// How often to look again for projects whose folder we have not asked about.
const SCAN_EVERY = 20000;

/** The last part of a path. */
function base(path) {
	const trimmed = path.replace(/\/+$/, "");
	return trimmed.slice(trimmed.lastIndexOf("/") + 1);
}

/** A project with no colour of its own still gets a steady one. */
function hue(id) {
	let sum = 0;
	for (let i = 0; i < id.length; i += 1) sum = (sum * 31 + id.charCodeAt(i)) >>> 0;
	return HUES[sum % HUES.length];
}

/** A project speaks up only when something in it needs you. */
function glyph(status) {
	return status === "blocked" || status === "done" ? "●" : "·";
}

export default class PowrSpaces extends View {
	init(_props, cx) {
		// Two roles, one mod: the list, and the question it puts on screen
		// when Browsr finds a project switched off.
		this.ask = props()?.ask ?? null;
		if (this.ask) {
			cx.notify();
			return;
		}
		this.seedBrowsers();
		this.answered = null; // the id of the last question dealt with
		cx.timer.every(ASK_EVERY, (cx) => this.pickUpQuestion(cx));
		this.seedFolders();
		// Renaming reuses one text field: only one folder is ever being
		// renamed, and a field per folder would keep as many alive.
		this.name = InputState.new({ placeholder: "Folder" });
		this.editing = null; // the folder being renamed
		this.menu = null; // the folder showing its colours and its delete
		this.spaceMenu = null; // the project showing its colours and its glyphs
		this.pressed = null; // the project under a held button, maybe a drag
		this.over = null; // { kind: "space" | "folder" | "out", id }
		this.dropped = false; // swallow the click that ends a drag
		this.name.on("submit", (_event, cx) => this.commitName(cx));
		this.name.on("blur", (_event, cx) => this.commitName(cx));
		// What repository each project's folder belongs to, and whether it is
		// a branch checked out beside it. Asked of git once per folder and
		// kept: it is what puts a worktree under the project it came from
		// instead of loose in the list.
		this.repos = {};
		this.scanning = false;
		this.scan(cx);
		cx.timer.every(SCAN_EVERY, (cx) => this.scan(cx));
		cx.notify();
	}

	/**
	 * Look for a question Browsr has left, and put it on screen.
	 *
	 * Polled rather than watched: one small file once a second, where a folder
	 * watch would wake on everything else Browsr writes in there.
	 */
	pickUpQuestion(cx) {
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
			openOverlay({ mod: "powr-spaces", kind: "dialog", width: 430, props: { ask: asked } });
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
	 * Where a project actually is, right now.
	 *
	 * Not the folder Herdr stores on it: that one only says where its *new*
	 * terminals would start, and on a list that has been lived in for a while
	 * it is wrong for most projects — it had this project pointing at the
	 * neighbour's repository. What the terminal you are looking at is in is
	 * the truth, and it is the thing that moves when you cd.
	 */
	cwdOf(space) {
		const ids = new Set((space.tabs || []).map((tab) => tab.id));
		const open = panes().filter((pane) => ids.has(pane.tab) && pane.cwd);
		const active = (space.tabs || []).find((tab) => tab.active);
		const first = (active && open.find((pane) => pane.tab === active.id)) || open[0];
		return first?.cwd || space.folder || null;
	}

	/** What git said about where this project sits, or null if we never asked. */
	gitOf(space) {
		const cwd = this.cwdOf(space);
		return (cwd ? this.repos[cwd] : null) ?? null;
	}

	/**
	 * The project a row belongs to: itself, unless it is a branch checked out
	 * beside one, in which case the project it is a branch of.
	 *
	 * A branch is not a thing you file away on its own — it is part of the
	 * project, and everything that applies to the project applies to it:
	 * which folder it is in, and what colour it wears. Put TasteRay in a
	 * folder and its branches go with it, because they were never separate.
	 */
	ownerOf(space) {
		const info = this.gitOf(space);
		if (!info || !info.linked) return space;
		return spaces().find((other) => {
			const theirs = this.gitOf(other);
			return theirs && !theirs.linked && theirs.repo === info.repo;
		}) ?? space;
	}

	/**
	 * Ask git about the folders we have not asked about yet.
	 *
	 * One call per folder, never repeated: a project's folder does not move,
	 * and the answer is only about where the folder sits, not what is in it.
	 * The shared directory names the repository; a checkout whose own top is
	 * not that directory's parent is a branch checked out beside it.
	 */
	scan(cx) {
		if (this.scanning) return;
		const todo = [];
		for (const space of spaces()) {
			const cwd = this.cwdOf(space);
			if (cwd && !(cwd in this.repos)) todo.push({ space, cwd });
		}
		if (!todo.length) return;
		this.scanning = true;
		cx.spawn(async (cx) => {
			for (const { space, cwd } of todo) {
				try {
					const r = await exec("git", [
						"rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel",
					], { cwd, on: space.machine ?? undefined });
					const [common, top] = String(r.stdout || "").trim().split("\n");
					if (r.code !== 0 || !common || !top) {
						this.repos[cwd] = null;
					} else {
						this.repos[cwd] = {
							repo: `${space.machine ?? ""}\u001f${common}`,
							name: common.endsWith("/.git") ? base(common.slice(0, -5)) : base(common),
							linked: common !== `${top}/.git`,
						};
					}
				} catch {
					// The machine is away. Leave it unasked so the next round
					// tries again rather than drawing it as "not a repository".
				}
			}
			this.scanning = false;
			cx.notify();
		});
	}

	/**
	 * The projects in the order they are drawn: each one, then the branches
	 * checked out beside it. A worktree belongs under the project it came
	 * from — that is the thing it is a branch of — and nowhere else in the
	 * list, however the order says otherwise.
	 */
	tree(list) {
		const out = [];
		const done = new Set();
		const git = (space) => this.gitOf(space);
		const drawn = new Set();
		for (const space of list) {
			const info = git(space);
			if (info && info.linked) continue; // drawn under its own project
			const kids = info && !done.has(info.repo)
				? list.filter((other) => {
					const theirs = git(other);
					return theirs && theirs.linked && theirs.repo === info.repo;
				})
				: [];
			if (info) done.add(info.repo);
			out.push({ space, kids });
			drawn.add(space.id);
			for (const kid of kids) drawn.add(kid.id);
		}
		// A worktree whose own project is not in this list would vanish.
		for (const space of list) {
			if (drawn.has(space.id)) continue;
			out.push({ space, kids: [] });
		}
		return out;
	}

	// ---- stored shape -------------------------------------------------

	/** Take the folders from Herdr's list once, then leave that copy alone. */
	seedFolders() {
		if (shared(FOLDERS)) return;
		const theirs = shared(HERDR_FOLDERS);
		const here = (theirs && typeof theirs === "object" ? theirs[""] : null) || {};
		setShared(FOLDERS, {
			list: Array.isArray(here.list) ? here.list.map((f) => ({ ...f })) : [],
			of: here.of && typeof here.of === "object" ? { ...here.of } : {},
		});
	}

	folders() {
		const data = shared(FOLDERS);
		if (!data || typeof data !== "object") return { list: [], of: {} };
		return {
			list: Array.isArray(data.list) ? data.list : [],
			of: data.of && typeof data.of === "object" ? data.of : {},
		};
	}

	save(next) {
		setShared(FOLDERS, { list: next.list, of: next.of });
	}

	/** The projects that have a browser, as { [id]: true }. */
	browsers() {
		const chosen = shared(ON);
		return chosen && typeof chosen === "object" ? chosen : {};
	}

	/**
	 * Carry the old list over, once.
	 *
	 * It said which projects were turned *off*, so reading it as the new one
	 * would take the browser away from everything at the same moment.
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

	/**
	 * A project's colour. A branch checked out beside one takes its project's
	 * colour rather than a colour of its own: the point of the tile is to say
	 * at a glance which project you are in, and three worktrees of the same
	 * repository in three different colours say the opposite.
	 *
	 * The stored colour is not consulted for a branch, because Herdr gives
	 * every project one whether or not anyone chose it: honouring those put
	 * three worktrees of one repository in three colours again.
	 */
	iconOf(space) {
		const stored = shared(SPACE_ICON_OF);
		return (stored && typeof stored === "object" ? stored[space.id] : null) || null;
	}

	wearSpaceIcon(id, icon, cx) {
		const stored = shared(SPACE_ICON_OF);
		const next = { ...(stored && typeof stored === "object" ? stored : {}) };
		if (icon) next[id] = icon;
		else delete next[id];
		setShared(SPACE_ICON_OF, next);
		cx.notify();
	}

	/** Herdr's own map, so the colour follows a project into every panel. */
	paintSpace(id, color, cx) {
		const stored = shared(HERDR_COLORS);
		const next = { ...(stored && typeof stored === "object" ? stored : {}) };
		if (color) next[id] = color;
		else delete next[id];
		setShared(HERDR_COLORS, next);
		cx.notify();
	}

	colorOf(space, from) {
		if (from) return this.colorOf(from, null);
		const stored = shared(HERDR_COLORS);
		const named = stored && typeof stored === "object" ? stored[space.id] : null;
		return MARK[named] ?? MARK[hue(space.id)];
	}

	// ---- folders ------------------------------------------------------

	newFolder(cx) {
		const { list, of } = this.folders();
		const id = `pf${Date.now().toString(36)}`;
		this.save({ list: [...list, { id, name: "New folder", folded: false, color: null }], of });
		// Straight into the name: a folder called "New folder" that you have
		// to find and rename afterwards is a folder you end up living with.
		this.editing = id;
		this.menu = null;
		this.name.set_value("New folder");
		this.name.focus();
		cx.notify();
	}

	commitName(cx) {
		const id = this.editing;
		if (!id) return;
		const typed = this.name.value().trim();
		const { list, of } = this.folders();
		this.editing = null;
		// An empty name is a cancelled rename, not a folder with no name —
		// except for one just made, which goes away again rather than sitting
		// there nameless.
		if (!typed) {
			const fresh = list.find((f) => f.id === id);
			if (fresh && fresh.name === "New folder") this.deleteFolder(id, cx);
			else cx.notify();
			return;
		}
		this.save({ of, list: list.map((f) => (f.id === id ? { ...f, name: typed } : f)) });
		cx.notify();
	}

	deleteFolder(id, cx) {
		const { list, of } = this.folders();
		// The projects in it are not deleted with it: they come back out as
		// loose projects. A folder is a way of looking at the list, not a
		// container that owns anything.
		const freed = Object.fromEntries(Object.entries(of).filter(([, f]) => f !== id));
		this.save({ list: list.filter((f) => f.id !== id), of: freed });
		this.menu = null;
		if (this.editing === id) this.editing = null;
		cx.notify();
	}

	paintFolder(id, color, cx) {
		const { list, of } = this.folders();
		this.save({ of, list: list.map((f) => (f.id === id ? { ...f, color } : f)) });
		cx.notify();
	}

	wearIcon(id, icon, cx) {
		const { list, of } = this.folders();
		this.save({ of, list: list.map((f) => (f.id === id ? { ...f, icon } : f)) });
		cx.notify();
	}

	toggleFolder(id, cx) {
		const { list, of } = this.folders();
		this.save({ of, list: list.map((f) => (f.id === id ? { ...f, folded: !f.folded } : f)) });
		cx.notify();
	}

	// ---- dragging -----------------------------------------------------

	/**
	 * Finish a drag. Dropping on a folder puts the project in it, dropping on
	 * another project puts it where that one is — in the same folder, and in
	 * Herdr's own order, which is what the rest of the app reads.
	 */
	drop(cx) {
		const dragged = this.pressed;
		const target = this.over;
		this.pressed = null;
		this.over = null;
		if (!dragged || !target) {
			cx.notify();
			return;
		}
		this.dropped = true;
		const { list, of } = this.folders();
		// Both ends of the drag are read as the project they belong to. Pick
		// up a branch and the whole project moves, drop onto a branch and you
		// have dropped onto its project: a branch has no place of its own.
		const projectOf = (id) => {
			const space = spaces().find((other) => other.id === id);
			return space ? this.ownerOf(space).id : id;
		};
		const moving = projectOf(dragged);
		const onto = target.kind === "space" ? projectOf(target.id) : target.id;
		// Anything a branch was filed under before this rule is dead weight.
		const clear = (map) => {
			const next = { ...map };
			if (dragged !== moving) delete next[dragged];
			return next;
		};
		if (target.kind === "folder") {
			this.save({ list, of: { ...clear(of), [moving]: onto } });
		} else if (target.kind === "out") {
			const freed = clear(of);
			delete freed[moving];
			this.save({ list, of: freed });
		} else if (onto !== moving) {
			const next = clear(of);
			if (of[onto]) next[moving] = of[onto];
			else delete next[moving];
			this.save({ list, of: next });
			const to = spaces().find((space) => space.id === onto);
			if (to) run("space.move", { id: moving, index: to.index });
		}
		cx.notify();
	}

	/** Mark what the pointer is over, but only while something is being dragged. */
	hover(kind, id, cx) {
		if (!this.pressed) return;
		if (this.over && this.over.kind === kind && this.over.id === id) return;
		this.over = { kind, id };
		cx.notify();
	}

	// ---- browser ------------------------------------------------------

	/**
	 * Give a project a browser, or take it away.
	 *
	 * Writing the list is only half of it. The browser is a separate program
	 * and hears nothing until it is told: without the second half, ticking a
	 * project here changed the list and left the strip exactly as it was —
	 * which reads as the tick simply not working, and sent people hunting
	 * for a group that was never made.
	 */
	toggleBrowser(space, cx) {
		const chosen = { ...this.browsers() };
		if (chosen[space]) delete chosen[space];
		else chosen[space] = true;
		setShared(ON, chosen);
		cx.spawn(async () => {
			// Nothing to report if it fails: the browser may not be running,
			// and the list is right either way — the strip is made to match
			// it again the moment it starts.
			try {
				await exec(BRIDGE, ["--app", "sync"]);
			} catch {}
		});
	}

	// ---- drawing ------------------------------------------------------

	row(t, space, inFront, mark, from) {
		const on = this.browsers()[space.id] === true;
		const letter = (space.name || "?").trim().charAt(0).toUpperCase() || "?";
		const icon = this.iconOf(space);
		const landing = this.over && this.over.kind === "space" && this.over.id === space.id;
		const lifted = this.pressed === space.id;
		return h_flex()
			.id(`powr-space-${space.id}`)
			.items_center()
			.gap(8)
			.px(8)
			.py(4)
			.rounded(6)
			.cursor_pointer()
			.when(inFront, (el) => el.bg(t.selected))
			.when(lifted, (el) => el.text_color(t.dim))
			// Where it would land, drawn on the row it would land on.
			.when(landing, (el) => el.border_t_2().border_color(t.accent))
			.hover((s) => s.bg(t.hover))
			.on_mouse_down("left", (_event, cx) => {
				this.pressed = space.id;
				cx.notify();
			})
			.on_mouse_move((_event, cx) => this.hover("space", space.id, cx))
			.on_mouse_up("left", (_event, cx) => this.drop(cx))
			.on_click((_event, cx) => {
				// The press that ended a drag is not also a press on a row.
				if (this.dropped) {
					this.dropped = false;
					return;
				}
				if (this.spaceMenu === space.id) {
					this.spaceMenu = null;
					cx.notify();
					return;
				}
				run("space.select", { id: space.id });
			})
			// Herdr's own menu, not one of ours: renaming, closing and the rest
			// are its business, and a second menu that did the same things
			// differently would be a worse list, not a better one.
			// Herdr's own menu, untouched. Rename, close, worktrees and the
			// rest are its business; ours is the tile, and that is what you
			// press to get at ours.
			.on_mouse_down("right", (_event, cx) => {
				cx.stop_propagation();
				openOverlay({
					mod: "menu", kind: "popup", width: 200, height: 260,
					props: { tab: "space", target: { id: space.id } },
				});
			})
			.when(this.spaceMenu === space.id, (el) =>
				el.on_mouse_down("left", (_event, cx) => cx.stop_propagation()))
			// A project's tile is filled; a branch beside it gets the same
			// tile drawn as an outline. Same shape and same colour say they
			// belong together; filled against hollow says which one is the
			// project, without either of them having to shrink or move.
			.child(
				h_flex()
					.id(`powr-tile-${space.id}`)
					.size(18)
					.flex_none()
					.rounded(5)
					.items_center()
					.justify_center()
					.cursor_pointer()
					.tooltip("Colour and glyph")
					.on_mouse_down("left", (_event, cx) => cx.stop_propagation())
					.on_click((_event, cx) => {
						cx.stop_propagation();
						this.spaceMenu = this.spaceMenu === space.id ? null : space.id;
						this.menu = null;
						cx.notify();
					})
					.when(!from, (el) => el.bg(this.colorOf(space, null)))
					.when(Boolean(from), (el) =>
						el.border_1().border_color(this.colorOf(space, from)))
					.child(icon
						? svg(`mods/powr-spaces/icons/${icon}.svg`).size(11)
							.text_color(from ? this.colorOf(space, from) : "#ffffff")
						: div().text_size(11)
							.text_color(from ? this.colorOf(space, from) : "#ffffff")
							.child(letter))
			)
			.child(
				v_flex()
					.flex_1()
					.min_w(0)
					.child(div().truncate().text_color(t.foreground).child(space.name))
					.when(Boolean(space.branch) && !TRUNK.has(space.branch)
						&& space.branch !== space.name, (el) =>
						el.child(div().truncate().text_size(10.5).text_color(t.dim).child(space.branch)))
			)
			.when(Boolean(space.status), (el) =>
				el.child(div().flex_none().text_color(t[space.status] ?? t.dim).child(glyph(space.status))))
			// The browser mark. Lit when this project has a group of tabs of its
			// own, barely there when it has not, and a press is the switch — the
			// whole reason this list is ours.
			.child(
				h_flex()
					.id(`powr-browser-${space.id}`)
					.size(20)
					.flex_none()
					.rounded(5)
					.items_center()
					.justify_center()
					.cursor_pointer()
					.tooltip(on ? "Has a browser. Press to take it away." : "No browser. Press to give it one.")
					.hover((s) => s.bg(t.surface))
					.on_mouse_down("left", (_event, cx) => cx.stop_propagation())
					.on_click((_event, cx) => {
						cx.stop_propagation();
						this.toggleBrowser(space.id, cx);
					})
					// As bright as a name and no brighter. In the accent colour
					// it was the loudest thing on the row, and it is the least
					// important thing on it.
					.child(svg("mods/powr-spaces/browser.svg").size(13)
						.text_color(on ? t.foreground : t.border))
			);
	}

	/**
	 * A project and the branches checked out beside it, as one group.
	 *
	 * Drawn as a line down the side rather than an elbow character per row.
	 * The characters sat in the gaps between rows and never joined up, so at
	 * this size the structure was barely there; a rule in the family's own
	 * colour says it in one stroke.
	 */
	group(t, { space, kids }, here, indent) {
		const block = v_flex().gap(1).child(this.row(t, space, space.id === here, null));
		if (!kids.length) return block;
		const line = this.colorOf(space, null);
		// Each row draws its own piece of the rule rather than the group
		// drawing one down its whole side: that one ran past the last branch
		// to the bottom of the group and pointed at the next project. Here
		// the last row's piece stops at its own stub, which is what an elbow
		// is. No gap between the rows either, or the pieces would not meet.
		const nest = v_flex().gap(0).ml(indent + 17);
		kids.forEach((kid, n) => {
			const last = n === kids.length - 1;
			nest.child(
				h_flex().items_stretch()
					.child(
						v_flex().w(11).flex_none().items_start()
							.child(div().w(1).flex_1().bg(line))
							.child(div().w(11).h(1).flex_none().bg(line))
							.child(last
								? div().w(1).flex_1()
								: div().w(1).flex_1().bg(line))
					)
					.child(div().flex_1().min_w(0)
						.child(this.row(t, kid, kid.id === here, null, space)))
			);
		});
		return block.child(nest);
	}

	folderHead(t, folder, inside) {
		const tint = folder.color ? (MARK[folder.color] ?? folder.color) : t.dim;
		const landing = this.over && this.over.kind === "folder" && this.over.id === folder.id;
		const head = h_flex()
			.id(`powr-folder-${folder.id}`)
			.items_center()
			.gap(8)
			.px(8)
			.py(4)
			.rounded(6)
			.cursor_pointer()
			.when(landing, (el) => el.bg(t.hover).border_1().border_color(t.accent))
			.hover((s) => s.bg(t.hover))
			.on_mouse_move((_event, cx) => this.hover("folder", folder.id, cx))
			.on_mouse_up("left", (_event, cx) => this.drop(cx))
			// Kept from the panel below, which closes its controls on any
			// press outside them: a press on this row is a press on their own
			// header, and it should put them away rather than fold the folder.
			.when(this.menu === folder.id, (el) =>
				el.on_mouse_down("left", (_event, cx) => cx.stop_propagation()))
			.on_click((_event, cx) => {
				if (this.dropped) {
					this.dropped = false;
					return;
				}
				if (this.menu === folder.id) {
					this.menu = null;
					cx.notify();
					return;
				}
				this.toggleFolder(folder.id, cx);
			})
			// Its own controls, in the row itself. A folder has three things you
			// can do to it and they fit where it stands; a popup for them would
			// be another mod to install and keep.
			.on_mouse_down("right", (_event, cx) => {
				cx.stop_propagation();
				this.menu = this.menu === folder.id ? null : folder.id;
				this.editing = null;
				cx.notify();
			})
			.child(
				// No tile under it. The glyph is already a folder, so a filled
			// square behind it only competes with the projects' own tiles,
			// which are the thing that is supposed to be read first.
			h_flex()
					.id(`powr-folder-tile-${folder.id}`)
					.size(18).flex_none().items_center().justify_center()
					.cursor_pointer()
					.tooltip("Colour and glyph")
					.on_mouse_down("left", (_event, cx) => cx.stop_propagation())
					.on_click((_event, cx) => {
						cx.stop_propagation();
						this.menu = this.menu === folder.id ? null : folder.id;
						this.spaceMenu = null;
						cx.notify();
					})
					.child(svg(folder.icon
						? `mods/powr-spaces/icons/${folder.icon}.svg`
						: "mods/powr-spaces/folder.svg").size(15).text_color(tint))
			);

		if (this.editing === folder.id) {
			head.child(
				div().flex_1().min_w(0).h(20).px(5).rounded(4)
					.border_1().border_color(t.accent)
					.cursor_text()
					.on_mouse_down("left", (_event, cx) => cx.stop_propagation())
					.child(Input.new(this.name).h_full().text_size(12).text_color(t.foreground))
			);
		} else {
			head
				.child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(folder.name))
				.child(div().flex_none().text_size(11).text_color(t.dim).child(String(inside.length)))
				.child(svg(`mods/powr-spaces/chevron-${folder.folded ? "right" : "down"}.svg`)
					.size(11).flex_none().text_color(t.dim));
		}
		return head;
	}

	/** A full-width row you can hit, the way Herdr's own menus are built. */
	item(t, id, label, handler) {
		return h_flex()
			.id(id)
			.h(28).px(8)
			.items_center()
			.rounded(6)
			.cursor_pointer()
			.text_color(t.foreground)
			.hover((s) => s.bg(t.hover))
			.on_click(handler)
			.child(label);
	}

	/** The box itself: the same frame for a folder and for a project. */
	box(t, title, rows) {
		// Drawn as a box, not as more list. Without an edge of its own it
		// read as part of the list underneath, and the words at the bottom
		// looked like headings over the projects below.
		const panel = v_flex()
			.gap(4).mx(2).mt(2).mb(4).p(6)
			.rounded(8)
			.bg(t.popover)
			.border_1()
			.border_color(t.border)
			.on_mouse_down("left", (_event, cx) => cx.stop_propagation())
			.on_mouse_down("right", (_event, cx) => cx.stop_propagation())
			// It stands away from the row it belongs to, so it says whose it
			// is — and the way out is up here, where a window's is.
			.child(
				h_flex().items_center().gap(6).px(8).pt(2).pb(1)
					.child(div().flex_1().min_w(0).truncate()
						.text_size(11).text_color(t.dim).child(title))
					.child(
						div()
							.id("powr-box-close")
							.px(5).rounded(4)
							.cursor_pointer()
							.text_size(12)
							.text_color(t.dim)
							.hover((s) => s.bg(t.hover).text_color(t.foreground))
							.on_click((_event, cx) => {
								this.menu = null;
								this.spaceMenu = null;
								cx.notify();
							})
							.child("\u00d7")
					)
			);
		for (const row of rows) panel.child(row);
		return panel;
	}

	/** Seven dots: the six Herdr paints with, and no colour at all. */
	swatches(t, current, pick) {
		const swatch = (color) =>
			div()
				.id(`powr-swatch-${current.key}-${color ?? "none"}`)
				.size(20).flex_none().rounded(10)
				.cursor_pointer()
				.bg(color ? MARK[color] : t.dim)
				.border_2()
				.border_color(current.color === color ? t.foreground : t.popover)
				.hover((s) => s.border_color(t.muted ?? t.dim))
				.on_click((_event, cx) => pick(color, cx));
		return h_flex().gap(6).items_center().px(8).py(4)
			.children([null, ...HUES].map(swatch));
	}

	/** The glyphs, as a wrapped grid. `plain` is the one that means none. */
	glyphs(t, { key, names, chosen, plain, pick, tint }) {
		const tile = (name) =>
			h_flex()
				.id(`powr-glyph-${key}-${name}`)
				.size(26).flex_none().rounded(6)
				.items_center().justify_center()
				.cursor_pointer()
				.tooltip(name === plain ? "plain" : name.replace(/^folder-/, "").replace(/-/g, " "))
				.when((chosen ?? plain) === name, (el) => el.bg(t.selected))
				.hover((s) => s.bg(t.hover))
				.on_click((_event, cx) => pick(name === plain ? null : name, cx))
				.child(svg(`mods/powr-spaces/icons/${name}.svg`).size(14)
					.text_color((chosen ?? plain) === name ? t.accent : tint));
		return h_flex().flex_wrap().gap(3).px(6).pb(2).children(names.map(tile));
	}

	/** The project a branch belongs to, or null when this is not a branch. */
	projectOf(space) {
		const cwd = this.cwdOf(space);
		const info = cwd ? this.repos[cwd] : null;
		if (!info || !info.linked) return null;
		return spaces().find((other) => {
			const theirs = this.cwdOf(other) ? this.repos[this.cwdOf(other)] : null;
			return theirs && !theirs.linked && theirs.repo === info.repo;
		}) ?? null;
	}

	spaceControls(t, space) {
		const stored = shared(HERDR_COLORS);
		const color = (stored && typeof stored === "object" ? stored[space.id] : null) ?? null;
		// A branch takes its project's colour, so offering it a colour of its
		// own would be offering something that does nothing. It says where the
		// colour comes from instead, which is the answer to the question
		// anyone opening this was about to ask.
		const project = this.projectOf(space);
		return this.box(t, space.name, [
			project
				? div().text_size(11).text_color(t.dim).px(8).py(4)
					.child(`colour comes from ${project.name}`)
				: this.swatches(t, { key: `sp-${space.id}`, color },
					(pick, cx) => this.paintSpace(space.id, pick, cx)),
			this.glyphs(t, {
				key: `sp-${space.id}`,
				names: SPACE_ICONS,
				chosen: this.iconOf(space),
				plain: null,
				tint: t.foreground,
				pick: (name, cx) => this.wearSpaceIcon(space.id, name, cx),
			}),
			div().h(1).mx(6).my(2).bg(t.border),
			this.item(t, `powr-sp-plain-${space.id}`, "Show its letter instead",
				(_event, cx) => this.wearSpaceIcon(space.id, null, cx)),
		]);
	}

	folderControls(t, folder) {
		return this.box(t, folder.name, [
			this.swatches(t, { key: `f-${folder.id}`, color: folder.color ?? null },
				(pick, cx) => this.paintFolder(folder.id, pick, cx)),
			this.glyphs(t, {
				key: `f-${folder.id}`,
				names: ICONS,
				chosen: folder.icon ?? null,
				plain: "folder",
				tint: t.foreground,
				pick: (name, cx) => this.wearIcon(folder.id, name, cx),
			}),
			div().h(1).mx(6).my(2).bg(t.border),
			this.item(t, `powr-folder-rename-${folder.id}`, "Rename folder\u2026", (_event, cx) => {
				this.editing = folder.id;
				this.menu = null;
				this.name.set_value(folder.name);
				this.name.focus();
				cx.notify();
			}),
			this.item(t, `powr-folder-delete-${folder.id}`, "Delete folder",
				(_event, cx) => this.deleteFolder(folder.id, cx)),
		]);
	}

	render() {
		const t = theme();
		if (this.ask) return this.renderAsk(t);
		const here = focused()?.space;
		const { list, of } = this.folders();
		const all = spaces();
		// Which folder a row is in — its project's folder, so a project and
		// the branches beside it are never split between two places.
		const folderOf = (space) => of[this.ownerOf(space).id];
		const inFolder = (space) => list.some((f) => f.id === folderOf(space));

		const body = v_flex().id("powr-list").flex_1().min_h(0).gap(1).px(6).pb(10)
			.overflow_y_scrollbar().mode("always");

		// Folders first, then the projects that are in none. A folder is
		// something you put away; the loose projects are the ones you are
		// actually in, and they belong where the eye lands.
		for (const folder of list) {
			const inside = this.tree(all.filter((space) => folderOf(space) === folder.id));
			// The number is projects, not rows: a project with three branches
			// beside it is one thing you put in here, not four.
			body.child(this.folderHead(t, folder, inside));
			if (folder.folded || !inside.length) continue;
			// What is in a folder sits on a faint wash of the folder's colour,
			// so it reads as belonging to it rather than as more of the list.
			// Faint, because the rows on it carry their own colours too.
			const tint = folder.color ? (MARK[folder.color] ?? folder.color) : t.foreground;
			const box = v_flex().gap(1).py(2).pl(8).rounded(6)
				.when(/^#[0-9a-f]{6}$/i.test(tint), (el) => el.bg(`${tint}14`));
			for (const node of inside) box.child(this.group(t, node, here, 0));
			body.child(box);
		}

		// Somewhere to drop a project that is in a folder, to take it out
		// again. Only while one is being dragged, and only while it is in one:
		// otherwise it is a strip of nothing across the list.
		const held = this.pressed ? all.find((space) => space.id === this.pressed) : null;
		if (held && folderOf(held)) {
			const out = this.over && this.over.kind === "out";
			body.child(
				h_flex()
					.id("powr-out")
					.h(22)
					.my(2)
					.items_center()
					.justify_center()
					.rounded(6)
					.border_1()
					.border_color(out ? t.accent : t.border)
					.text_size(11)
					.text_color(out ? t.foreground : t.dim)
					.on_mouse_move((_event, cx) => this.hover("out", "out", cx))
					.on_mouse_up("left", (_event, cx) => this.drop(cx))
					.child("out of its folder")
			);
		}

		for (const node of this.tree(all.filter((space) => !inFolder(space)))) {
			body.child(this.group(t, node, here, 0));
		}

		const folderOpen = list.find((f) => f.id === this.menu);
		const spaceOpen = this.spaceMenu ? all.find((s) => s.id === this.spaceMenu) : null;
		const open = folderOpen
			? this.folderControls(t, folderOpen)
			: (spaceOpen ? this.spaceControls(t, spaceOpen) : null);

		const chosen = this.browsers();
		const off = all.filter((space) => chosen[space.id] !== true).length;
		const button = (id, label, hint, handler) =>
			div()
				.id(id)
				.px(5)
				.rounded(5)
				.cursor_pointer()
				.text_color(t.dim)
				.tooltip(hint)
				.hover((s) => s.bg(t.hover).text_color(t.foreground))
				.on_click(handler)
				.child(label);

		return v_flex()
			.id("root")
			.size_full()
			.bg(t.background)
			.py(8)
			.gap(6)
			.text_size(12.5)
			// A button let go anywhere else ends the drag too, so a drop that
			// misses every row leaves the list as it was instead of leaving a
			// project stuck to the pointer.
			.on_mouse_up("left", (_event, cx) => {
				if (this.pressed) this.drop(cx);
			})
			// A press anywhere but inside a folder's own controls closes them:
			// elsewhere in this list, and — the second handler — anywhere
			// else in the window at all, which is where a press that means
			// "I am done with this" usually lands.
			.on_mouse_down("left", (_event, cx) => {
				if (this.menu === null && this.spaceMenu === null) return;
				this.menu = null;
				this.spaceMenu = null;
				cx.notify();
			})
			.on_mouse_down_out((_event, cx) => {
				if (this.menu === null && this.spaceMenu === null && this.editing === null) return;
				this.menu = null;
				this.spaceMenu = null;
				// A name half typed is kept, not thrown away: it is the same
				// as clicking off a field, which is how a rename is finished
				// everywhere else.
				if (this.editing !== null) this.commitName(cx);
				cx.notify();
			})
			.child(
				h_flex().px(14).items_center().gap(4)
					.child(div().flex_1().text_size(11).text_color(t.dim).child("spaces"))
					.when(off > 0, (el) => el.child(
						div().text_size(10.5).text_color(t.dim).child(`${off} without a browser`)))
					.child(button("powr-new-folder",
						svg("mods/powr-spaces/folder.svg").size(12), "New folder",
						(_event, cx) => this.newFolder(cx)))
					.child(button("powr-new-space", "+", "New space", () => run("space.new")))
			)
			.child(body)
			// Under the list, not inside it. Drawn next to its own row it was
			// cut off by the bottom of the panel for anything near the end of
			// the list — half of it behind the agents below, with no way to
			// scroll to the rest. Here it is whole wherever the row is, and
			// the list keeps its place instead of jumping as the box opens.
			.when(Boolean(open), (el) => el.child(open));
	}
}
