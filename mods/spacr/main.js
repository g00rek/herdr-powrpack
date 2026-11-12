import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex, InputState, Input } from "gpui-base";
import { spaces, panes, focused, theme, run, shared, setShared, openOverlay, closeOverlay,
	exec, props, readTextFile, writeTextFile, home, appHome, nextCommand } from "herdr";

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

// Everything the colour menu offers: Herdr's six by name, the rest as plain
// #rrggbb, which Herdr stores and paints the same way in every panel. Laid
// out round the colour wheel, nine dots to a row with "none" first, so a
// colour is found by where it sits rather than by reading. The added ones
// keep the six's weight, dark enough for a white letter on top.
const PALETTE = [
	"#d9534f", "ansi3", "#d9922e", "#c4a42c", "#7fae3a", "ansi2", "ansi6", "#2aa5c9",
	"ansi4", "#3f62b8", "#6366e0", "ansi5", "#b25bd6", "ansi1", "#a0714a", "#7d8796", "#545b68",
];
const SWATCHES_PER_ROW = 9;
// The colour menu's height, px: its rows of dots and the space between them.
const SWATCH_ROWS = Math.ceil((PALETTE.length + 1) / SWATCHES_PER_ROW);
const SWATCH_HEIGHT = 8 + SWATCH_ROWS * 20 + (SWATCH_ROWS - 1) * 6 + 8;

/** What a stored colour looks like: one of Herdr's names, or a #rrggbb. */
function paint(color) {
	return MARK[color] ?? color;
}

// Our own folders, kept apart from the ones Herdr's list uses. Seeded from
// those once, so the folders you already have are here on the first run, and
// never written back: nothing we do can damage the list you can switch to.
// What a folder can wear. Every one of them is a folder — the shape says
// "this holds projects" and nothing else should have to. They differ only in
// the mark on the flap, which is the part that says which folder it is.
// Lucide, shipped in this mod's own icons folder with the ISC licence.
const ICONS = [
	"folder", "folder-archive", "folder-bookmark", "folder-check",
	"folder-clock", "folder-closed", "folder-code", "folder-cog",
	"folder-dot", "folder-down", "folder-git", "folder-git-2",
	"folder-heart", "folder-input", "folder-kanban", "folder-key",
	"folder-lock", "folder-minus", "folder-open", "folder-open-dot",
	"folder-output", "folder-pen", "folder-plus", "folder-root",
	"folder-search", "folder-search-2", "folder-symlink", "folder-sync",
	"folder-tree", "folder-up", "folder-user", "folder-users", "folder-x",
	"folders",
];

// What a project can wear instead of its initial. A project is anything, so
// these are not all one shape the way the folder glyphs are. These are what
// the menu shows before anything is typed; typing searches all of Lucide,
// by name and by the words Lucide files each icon under.
// How many glyphs fit across a popup 264 wide: 26 each and 3 between.
const GLYPHS_PER_ROW = 8;
// The project icon grid scrolls inside this much: six rows.
const GRID_HEIGHT = 6 * 29;

// Every Lucide icon shipped here, as [name, the words it is filed under].
const ICON_INDEX = `${appHome()}/mods/spacr/icons/index.json`;
// How many matches a search shows. More than this is a word too short to
// mean anything yet, and drawing two thousand at once is not free.
const MATCHES = 120;

const SPACE_ICONS = [
	"code", "braces", "terminal", "square-terminal", "rocket", "bug",
	"database", "server", "shield", "globe", "cloud", "cpu", "smartphone",
	"monitor", "laptop", "tablet", "gamepad-2", "joystick", "camera",
	"film", "clapperboard", "video", "headphones", "mic", "palette",
	"pen-tool", "brush", "feather", "book", "book-open", "notebook-pen",
	"graduation-cap", "school", "banknote", "wallet", "coins",
	"piggy-bank", "chart-column", "chart-line", "chart-pie", "trending-up",
	"briefcase", "store", "shopping-cart", "mail", "message-circle",
	"phone", "users", "user", "heart", "star", "sparkles", "flask-conical",
	"atom", "brain", "microscope", "dna", "leaf", "tree-pine", "flower-2",
	"sun", "moon", "flame", "zap", "wrench", "hammer", "cog", "package",
	"box", "truck", "plane", "car", "bike", "ship", "bot", "cat", "dog",
	"bird", "fish", "rabbit", "paw-print", "map-pin", "map", "compass",
	"calendar", "clock", "timer", "bell", "lock", "key", "tent",
	"mountain", "coffee", "utensils", "pizza", "apple", "gift", "trophy",
	"medal", "target", "puzzle", "dice-5", "ghost", "skull", "crown",
	"lightbulb", "newspaper", "scroll",
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

// The pack's module switches, set in the Powr panel. Browsr is on unless it
// says otherwise.
const MODULES = "powr.modules";

// What a branch wears until someone picks otherwise: Herdr's own branch mark,
// the one its list puts beside a worktree.
const BRANCH_ICON = "branch";

// A project name's line, px. A branch's icon and the rule's stub both sit on
// its middle, so they meet the name whether or not a line follows it.
const NAME_LINE = 20;

// Browsr's own command, for the one thing this list changes that lives
// outside Herdr. Change this line if your checkout is elsewhere.
const BRIDGE = `${home()}/Projects/herdr-powrpack/bridge.py`;

// Branches worth printing under a name. Almost every project sits on its
// trunk, so printing it on every row costs a second line each and tells you
// nothing; a branch is news only when it is not the trunk.
const TRUNK = new Set(["main", "master", "trunk", "develop"]);

// How often to look for spaces git has not been asked about: often, so a new
// one is placed before anyone sees it; asking is skipped when there is none.
const NEW_EVERY = 250;
// The longest a new space stays hidden while git is asked about it.
const NEW_WAIT = 2000;

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
		// One mod, several roles: the list; and the two popups the
		// list opens at the pointer on a right press — a project's icon menu
		// (colour and glyph; the rest of the row keeps Herdr's own menu), and
		// a folder's one menu (name, colour, glyph, delete). A left press
		// only ever selects a project or folds a folder.
		this.role = props()?.role ?? null;
		if (this.role === "look") {
			this.query = "";
			this.index = [];
			this.search = InputState.new({ placeholder: "Search all icons" });
			this.search.on("change", (_event, cx) => {
				this.query = this.search.value().trim().toLowerCase();
				cx.notify();
			});
			this.search.focus();
			cx.spawn(async (cx) => {
				try {
					this.index = JSON.parse(await readTextFile(ICON_INDEX));
				} catch {}
				cx.notify();
			});
			cx.notify();
			return;
		}
		if (this.role === "folder") {
			const folder = this.folders().list.find((f) => f.id === props().id);
			this.name = InputState.new({ placeholder: "Folder" });
			this.name.set_value(folder?.name ?? "");
			this.name.on("submit", (_event, cx) => this.renameAndClose(cx));
			// Clicking a colour after typing a name keeps the name.
			this.name.on("blur", (_event, cx) => this.rename(cx));
			this.name.focus();
			cx.notify();
			return;
		}
		this.seedBrowsers();
		this.seedFolders();
		// Renaming reuses one text field: only one folder is ever being
		// renamed, and a field per folder would keep as many alive.
		this.name = InputState.new({ placeholder: "Folder" });
		this.editing = null; // the folder being renamed
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
		// A space made while the list is up waits, a moment at most, for git
		// to say what it is — so a worktree appears under its project instead
		// of loose first and pulled in later.
		this.shown = new Set(spaces().map((space) => space.id));
		this.born = {};
		this.scan(cx);
		cx.timer.every(NEW_EVERY, (cx) => {
			this.scan(cx);
			if (spaces().some((space) => !this.shown.has(space.id))) cx.notify();
		});
		// `spacr.reset-branch-icons`, from a key or the command line.
		cx.spawn(async (cx) => {
			for (;;) {
				const command = await nextCommand();
				if (command === "reset-branch-icons") this.resetBranchIcons(cx);
			}
		});
		cx.notify();
	}

	/** The folder background's strength, as hex alpha; null for none. Set in Powr. */
	wash() {
		const strength = { none: null, light: "14", medium: "22", strong: "38" };
		const chosen = shared("spacr.wash");
		return chosen in strength ? strength[chosen] : strength.medium;
	}

	/** Whether Browsr is switched on in the Powr panel. */
	browsrOn() {
		const modules = shared(MODULES);
		return !(modules && typeof modules === "object" && modules.browsr === false);
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
		const memo = this.memo;
		if (memo?.cwd.has(space.id)) return memo.cwd.get(space.id);
		const ids = new Set((space.tabs || []).map((tab) => tab.id));
		const open = (memo?.panes ?? panes()).filter((pane) => ids.has(pane.tab) && pane.cwd);
		const active = (space.tabs || []).find((tab) => tab.active);
		const first = (active && open.find((pane) => pane.tab === active.id)) || open[0];
		const cwd = first?.cwd || space.folder || null;
		memo?.cwd.set(space.id, cwd);
		return cwd;
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
		const memo = this.memo;
		if (memo?.owner.has(space.id)) return memo.owner.get(space.id);
		const info = this.gitOf(space);
		const owner = !info || !info.linked ? space
			: (memo?.spaces ?? spaces()).find((other) => {
				const theirs = this.gitOf(other);
				return theirs && !theirs.linked && theirs.repo === info.repo;
			}) ?? space;
		memo?.owner.set(space.id, owner);
		return owner;
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
		// Runs four times a second: read the panes once, not once per space.
		const outer = this.memo;
		this.memo = outer ?? { spaces: spaces(), panes: panes(), cwd: new Map(), owner: new Map() };
		for (const space of this.memo.spaces) {
			const cwd = this.cwdOf(space);
			if (cwd && !(cwd in this.repos)) todo.push({ space, cwd });
		}
		this.memo = outer;
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
	/** The icon picked for a project, or null. */
	pickedIcon(space) {
		const stored = shared(SPACE_ICON_OF);
		return (stored && typeof stored === "object" ? stored[space.id] : null) || null;
	}

	/** What a row wears: its pick, else Herdr's branch mark for a branch. */
	iconOf(space, from) {
		return this.pickedIcon(space) || (from ? BRANCH_ICON : null);
	}

	/** Put every branch back on the branch mark. */
	resetBranchIcons(cx) {
		const branches = spaces().filter((space) => this.ownerOf(space).id !== space.id);
		const stored = shared(SPACE_ICON_OF);
		const next = { ...(stored && typeof stored === "object" ? stored : {}) };
		for (const space of branches) delete next[space.id];
		setShared(SPACE_ICON_OF, next);
		cx.notify();
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
		return named ? paint(named) : MARK[hue(space.id)];
	}

	// ---- folders ------------------------------------------------------

	newFolder(cx) {
		const { list, of } = this.folders();
		const id = `pf${Date.now().toString(36)}`;
		this.save({ list: [...list, { id, name: "New folder", folded: false, color: null }], of });
		// Straight into the name: a folder called "New folder" that you have
		// to find and rename afterwards is a folder you end up living with.
		this.editing = id;
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
		// Off is written down, not forgotten: a space missing from the list
		// is a new one, and Powr turns new ones on when its setting says so.
		const chosen = { ...this.browsers(), [space]: this.browsers()[space] !== true };
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
		const icon = this.iconOf(space, from);
		const landing = this.over && this.over.kind === "space" && this.over.id === space.id;
		const lifted = this.pressed === space.id;
		return h_flex()
			.id(`powr-space-${space.id}`)
			// A branch lines up with its name, the line the rule points at;
			// a project's larger tile sits in the middle of its two lines.
			.when(!from, (el) => el.items_center().px(8))
			.when(Boolean(from), (el) => el.items_start().pl(2).pr(8))
			.gap(8)
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
				run("space.select", { id: space.id });
			})
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
			// A project's tile is filled; a branch beside it shows only its
			// icon, in the project's colour, with no tile around it — as
			// Herdr's own list draws a worktree.
			.child(
				h_flex()
					.id(`powr-tile-${space.id}`)
					// A project's tile is as large as a folder's icon, so the two
					// stand in one column; a branch's stays small under it.
					.w(from ? 18 : 22).h(from ? NAME_LINE : 22)
					.flex_none()
					.rounded(from ? 5 : 6)
					.items_center()
					.justify_center()
					.cursor_pointer()
					.tooltip("Right-click: colour and glyph")
					// A right press here is the icon's own menu, and stops
					// before the row's — which is Herdr's. A left press is
					// not caught at all: it selects, like anywhere on the row.
					.on_mouse_down("right", (_event, cx) => {
						cx.stop_propagation();
						this.openLook(space.id, from?.name ?? null);
					})
					.when(!from, (el) => el.bg(this.colorOf(space, null)))
					.child(icon
						? svg(`mods/spacr/icons/${icon}.svg`).size(from ? 13 : 14)
							.text_color(from ? this.colorOf(space, from) : "#ffffff")
						: div().text_size(from ? 11 : 13)
							.text_color(from ? this.colorOf(space, from) : "#ffffff")
							.child(letter))
			)
			.child(
				v_flex()
					.flex_1()
					.min_w(0)
					.child(div().truncate().line_height(`${NAME_LINE}px`).text_color(t.foreground).child(space.name))
					.when(Boolean(space.branch) && !TRUNK.has(space.branch)
						&& space.branch !== space.name, (el) =>
						el.child(div().truncate().text_size(10.5).text_color(t.dim).child(space.branch)))
			)
			.when(Boolean(space.status), (el) =>
				el.child(div().flex_none().text_color(t[space.status] ?? t.dim).child(glyph(space.status))))
			// The browser mark. Lit when this project has a group of tabs of its
			// own, barely there when it has not, and a press is the switch — the
			// whole reason this list is ours.
			.when(this.browsrOn(), (el) => el.child(
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
					.child(svg("mods/spacr/browser.svg").size(13)
						.text_color(on ? t.foreground : t.border))
			));
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
		return block.child(this.branches(t, kids.map((kid) =>
			this.row(t, kid, kid.id === here, null, space)), this.colorOf(space, null),
			indent, 4 + NAME_LINE / 2));
	}

	/**
	 * Rows hung off the one above them by a rule: a line down from its tile
	 * and a short stub into each row. The same drawing for a project's
	 * branches and for the projects in a folder, so the whole list reads as
	 * one tree.
	 *
	 * Each row draws its own piece of the rule rather than one line down the
	 * whole side: that one ran past the last row and pointed at whatever came
	 * next. Here the last row's piece stops at its own stub, which is what an
	 * elbow is. No gap between the rows either, or the pieces would not meet.
	 * `reach` is how far down a row its stub sits: the middle of its tile.
	 */
	branches(t, rows, line, indent, reach) {
		const nest = v_flex().gap(0).ml(indent + 17);
		rows.forEach((child, n) => {
			const last = n === rows.length - 1;
			nest.child(
				h_flex().items_stretch()
					.child(
						v_flex().w(17).flex_none().items_start()
							.child(div().w(1).h(reach).flex_none().bg(line))
							.child(div().w(17).h(1).flex_none().bg(line))
							.child(last
								? div().w(1).flex_1()
								: div().w(1).flex_1().bg(line))
					)
					.child(div().flex_1().min_w(0).child(child))
			);
		});
		return nest;
	}

	folderHead(t, folder, inside) {
		const tint = folder.color ? paint(folder.color) : t.dim;
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
			.on_click((_event, cx) => {
				if (this.dropped) {
					this.dropped = false;
					return;
				}
				this.toggleFolder(folder.id, cx);
			})
			// Its own controls, in the row itself. A folder has three things you
			// can do to it and they fit where it stands; a popup for them would
			// be another mod to install and keep.
			.on_mouse_down("right", (_event, cx) => {
				cx.stop_propagation();
				const rows = Math.ceil(ICONS.length / GLYPHS_PER_ROW);
				openOverlay({
					mod: "spacr", kind: "popup", width: 264,
					height: 30 + 36 + SWATCH_HEIGHT + Math.min(rows * 29, GRID_HEIGHT) + 10 + 36,
					props: { role: "folder", id: folder.id },
				});
			})
			.child(
				// A tile like a project's, so the tree has one shape at every
				// level, but a faint one: the projects in it are read first.
			h_flex()
					.id(`powr-folder-tile-${folder.id}`)
					.size(22).flex_none().rounded(6).items_center().justify_center()
					.cursor_pointer()
					.when(/^#[0-9a-f]{6}$/i.test(tint), (el) => el.bg(`${tint}40`))
					.child(svg(folder.icon
						? `mods/spacr/icons/${folder.icon}.svg`
						: "mods/spacr/folder.svg").size(14).text_color(tint))
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
				.child(svg(`mods/spacr/chevron-${folder.folded ? "right" : "down"}.svg`)
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

	/**
	 * Open how a project or a folder looks, at the pointer.
	 *
	 * A popup, the way Herdr's own menu opens: where you pressed, kept inside
	 * the window by Herdr, and gone with a press beside it or Esc. Drawn into
	 * the list instead, it either fell off the bottom behind the agents or —
	 * moved under the list to stop that — opened somewhere far from the row
	 * it was about.
	 */
	openLook(id, from) {
		openOverlay({
			mod: "spacr", kind: "popup", width: 264,
			height: 30 + (from ? 26 : SWATCH_HEIGHT) + 36 + GRID_HEIGHT + 14,
			props: { role: "look", id, from },
		});
	}

	/** A popup's content: its title, then its rows. */
	box(t, title, rows) {
		const panel = v_flex().size_full().gap(4).p(6).bg(t.popover)
			.child(h_flex().items_center().pl(8).pt(2).pb(1)
				.child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(title))
				.child(h_flex().id("powr-close").size(20).flex_none().rounded(5)
					.items_center().justify_center().cursor_pointer()
					.text_color(t.dim)
					.hover((s) => s.bg(t.hover).text_color(t.foreground))
					.on_click((_event, cx) => {
						// A folder's name typed but not yet confirmed is kept.
						if (this.role === "folder") this.rename(cx);
						closeOverlay(props().overlay);
					})
					.child(svg("mods/spacr/icons/x.svg").size(12))));
		for (const row of rows) panel.child(row);
		return panel;
	}

	/** The palette as dots, nine to a row, after one for no colour at all. */
	swatches(t, current, pick) {
		const swatch = (color) =>
			div()
				.id(`powr-swatch-${current.key}-${color ? color.replace("#", "") : "none"}`)
				.size(20).flex_none().rounded(10)
				.cursor_pointer()
				.bg(color ? paint(color) : t.dim)
				.border_2()
				.border_color(current.color === color ? t.foreground : t.popover)
				.hover((s) => s.border_color(t.muted ?? t.dim))
				.on_click((_event, cx) => pick(color, cx));
		return h_flex().flex_wrap().gap(6).items_center().px(8).py(4)
			.children([null, ...PALETTE].map(swatch));
	}

	/** The glyphs, as a wrapped grid. `plain` is the one that means none. */
	glyphs(t, { key, names, chosen, plain, pick, tint, scroll, letter }) {
		const tile = (name) =>
			h_flex()
				.id(`powr-glyph-${key}-${name}`)
				.size(26).flex_none().rounded(6)
				.items_center().justify_center()
				.cursor_pointer()
				.tooltip(name === plain && name === "folder" ? "plain" : name.replace(/^folder-/, "").replace(/-/g, " "))
				.when((chosen ?? plain) === name, (el) => el.bg(t.selected))
				.hover((s) => s.bg(t.hover))
				.on_click((_event, cx) => pick(name === plain ? null : name, cx))
				.child(svg(`mods/spacr/icons/${name}.svg`).size(14)
					.text_color((chosen ?? plain) === name ? t.accent : tint));
		// A project's own letter is the first choice: what it wears with no
		// icon at all, picked the same way as any icon.
		const own = letter && h_flex()
			.id(`powr-glyph-${key}-letter`)
			.size(26).flex_none().rounded(6)
			.items_center().justify_center()
			.cursor_pointer()
			.when(!chosen, (el) => el.bg(t.selected))
			.hover((s) => s.bg(t.hover))
			.on_click((_event, cx) => pick(null, cx))
			.child(div().text_size(12).text_color(!chosen ? t.accent : tint).child(letter));
		const grid = h_flex().flex_wrap().gap(3).px(6).pb(2)
			.when(Boolean(own), (el) => el.child(own))
			.children(names.map(tile));
		if (!scroll) return grid;
		return v_flex().id(`powr-glyphs-${key}`).h(scroll).overflow_y_scrollbar().child(grid);
	}

	/**
	 * Icons matching what was typed: names that start with it first, then
	 * names that contain it, then icons Lucide files under a word starting
	 * with it — "music" finds the notes and the headphones, not only icons
	 * with music in their name.
	 */
	matches(query) {
		const starts = [], within = [], tagged = [];
		for (const [name, words] of this.index) {
			if (name.startsWith(query)) starts.push(name);
			else if (name.includes(query)) within.push(name);
			else if (` ${words}`.includes(` ${query}`)) tagged.push(name);
			if (starts.length >= MATCHES) break;
		}
		return [...starts, ...within, ...tagged].slice(0, MATCHES);
	}

	spaceControls(t, space) {
		const stored = shared(HERDR_COLORS);
		const color = (stored && typeof stored === "object" ? stored[space.id] : null) ?? null;
		// A branch takes its project's colour, so offering it a colour of its
		// own would be offering something that does nothing. It says where the
		// colour comes from instead, which is the answer to the question
		// anyone opening this was about to ask.
		const from = props()?.from ?? null;
		return this.box(t, space.name, [
			from
				? div().text_size(11).text_color(t.dim).px(8).py(4)
					.child(`colour comes from ${from}`)
				: this.swatches(t, { key: `sp-${space.id}`, color },
					(pick, cx) => this.paintSpace(space.id, pick, cx)),
			h_flex().h(28).mx(4).px(6).items_center().rounded(6)
				.border_1().border_color(t.border)
				.child(Input.new(this.search).h_full().text_size(12).text_color(t.foreground)),
			this.query && !this.matches(this.query).length
				? div().px(8).py(6).text_size(11).text_color(t.dim).child("nothing by that name")
				: this.glyphs(t, {
				key: `sp-${space.id}`,
				scroll: GRID_HEIGHT,
				// A branch's first choice is the branch mark it wears unpicked;
				// a project's is its own letter.
				names: this.query ? this.matches(this.query)
					: from ? [BRANCH_ICON, ...SPACE_ICONS] : SPACE_ICONS,
				chosen: this.pickedIcon(space),
				plain: from ? BRANCH_ICON : null,
				letter: from ? null : (space.name || "?").trim().charAt(0).toUpperCase() || "?",
				tint: t.foreground,
				pick: (name, cx) => this.wearSpaceIcon(space.id, name, cx),
			}),
		]);
	}

	/** A folder's one menu: its name, its colour, its glyph, and Delete. */
	renderFolderMenu(t) {
		const id = props()?.id;
		const folder = this.folders().list.find((f) => f.id === id);
		if (!folder) return div();
		return this.box(t, "Folder", [
			h_flex().h(28).mx(4).px(6).items_center().rounded(6)
				.border_1().border_color(t.accent)
				.child(Input.new(this.name).h_full().text_size(12).text_color(t.foreground)),
			this.swatches(t, { key: `f-${folder.id}`, color: folder.color ?? null },
				(pick, cx) => this.paintFolder(folder.id, pick, cx)),
			this.glyphs(t, {
				key: `f-${folder.id}`,
				names: ICONS,
				scroll: Math.min(Math.ceil(ICONS.length / GLYPHS_PER_ROW) * 29, GRID_HEIGHT),
				chosen: folder.icon ?? null,
				plain: "folder",
				tint: t.foreground,
				pick: (name, cx) => this.wearIcon(folder.id, name, cx),
			}),
			div().h(1).mx(6).my(2).bg(t.border),
			this.item(t, `powr-folder-delete-${id}`, "Delete folder", (_event, cx) => {
				this.deleteFolder(id, cx);
				closeOverlay(props().overlay);
			}),
		]);
	}

	rename(cx) {
		const id = props()?.id;
		const typed = this.name.value().trim();
		const { list, of } = this.folders();
		const now = list.find((f) => f.id === id);
		if (typed && now && now.name !== typed) {
			this.save({ of, list: list.map((f) => (f.id === id ? { ...f, name: typed } : f)) });
		}
		cx.notify();
	}

	renameAndClose(cx) {
		this.rename(cx);
		closeOverlay(props().overlay);
	}

	renderLook(t) {
		const { id } = props() ?? {};
		const space = spaces().find((other) => other.id === id);
		return space ? this.spaceControls(t, space) : div();
	}

	render() {
		const t = theme();
		if (this.role === "look") return this.renderLook(t);
		if (this.role === "folder") return this.renderFolderMenu(t);
		// Herdr stops a render that runs too long. Which folder and which
		// project each row belongs to is asked many times over in one pass,
		// and each ask read every pane and every space again; within one
		// render the answers cannot change, so they are worked out once.
		this.memo = { spaces: spaces(), panes: panes(), cwd: new Map(), owner: new Map() };
		try {
			return this.renderList(t);
		} finally {
			this.memo = null;
		}
	}

	renderList(t) {
		const here = focused()?.space;
		const { list, of } = this.folders();
		const now = Date.now();
		const all = this.memo.spaces.filter((space) => {
			if (this.shown.has(space.id)) return true;
			this.born[space.id] ??= now;
			const cwd = this.cwdOf(space);
			if ((cwd && cwd in this.repos) || now - this.born[space.id] > NEW_WAIT) {
				this.shown.add(space.id);
				return true;
			}
			return false;
		});
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
			// A folder and what is in it sit on one faint wash of the folder's
			// colour, its own row included, so the whole thing reads as one
			// place rather than as more of the list. Faint, because the rows
			// on it carry their own colours too.
			const tint = folder.color ? paint(folder.color) : t.foreground;
			const card = v_flex().gap(1).rounded(6)
				.when(/^#[0-9a-f]{6}$/i.test(tint) && this.wash() !== null, (el) => el.bg(`${tint}${this.wash()}`))
				.child(this.folderHead(t, folder, inside));
			if (!folder.folded && inside.length) {
				// Its projects hang off its tile by the same rule a project's
				// branches hang off the project, in the folder's colour.
				card.child(this.branches(t, inside.map((node) => this.group(t, node, here, 0)),
					tint, 0, 4 + 11).pb(2));
			}
			body.child(card);
		}

		for (const node of this.tree(all.filter((space) => !inFolder(space)))) {
			body.child(this.group(t, node, here, 0));
		}
		// The empty list below the last project is outside every folder, so a
		// project dropped there leaves its folder — the way a file dropped
		// beside the folders in a file manager does. Dropping it onto a loose
		// project does the same and also puts it in that place.
		body.child(div().id("powr-loose").flex_1().min_h(32)
			.on_mouse_move((_event, cx) => this.hover("out", "out", cx))
			.on_mouse_up("left", (_event, cx) => this.drop(cx)));

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
			// A press anywhere else finishes a name being typed into a new
			// folder, the way clicking off a field finishes it everywhere.
			.on_mouse_down_out((_event, cx) => {
				if (this.editing !== null) this.commitName(cx);
			})
			.child(
				h_flex().px(14).items_center().gap(4)
					.child(div().flex_1().text_size(11).text_color(t.dim).child("spaces"))

					.child(button("powr-new-folder",
						svg("mods/spacr/folder.svg").size(12), "New folder",
						(_event, cx) => this.newFolder(cx)))
					.child(button("powr-new-space", "+", "New space", () => run("space.new")))
			)
			.child(body);
	}
}
