import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex, InputState, Input } from "gpui-base";
import { spaces, focused, theme, run, shared, setShared, openOverlay } from "herdr";

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
const FOLDERS = "powr.folders";
const HERDR_FOLDERS = "space-folders";

// Which projects have no browser: Browsr's own setting, shared with its
// panel so the two can never disagree.
const SKIP = "browsr.skip";

// Branches worth printing under a name. Almost every project sits on its
// trunk, so printing it on every row costs a second line each and tells you
// nothing; a branch is news only when it is not the trunk.
const TRUNK = new Set(["main", "master", "trunk", "develop"]);

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
		this.seedFolders();
		// Renaming reuses one text field: only one folder is ever being
		// renamed, and a field per folder would keep as many alive.
		this.name = InputState.new({ placeholder: "Folder" });
		this.editing = null; // the folder being renamed
		this.menu = null; // the folder showing its colours and its delete
		this.pressed = null; // the project under a held button, maybe a drag
		this.over = null; // { kind: "space" | "folder" | "out", id }
		this.dropped = false; // swallow the click that ends a drag
		this.name.on("submit", (_event, cx) => this.commitName(cx));
		this.name.on("blur", (_event, cx) => this.commitName(cx));
		cx.notify();
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

	skipped() {
		const chosen = shared(SKIP);
		return chosen && typeof chosen === "object" ? chosen : {};
	}

	colorOf(space) {
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
		const moving = this.pressed;
		const target = this.over;
		this.pressed = null;
		this.over = null;
		if (!moving || !target) {
			cx.notify();
			return;
		}
		this.dropped = true;
		const { list, of } = this.folders();
		if (target.kind === "folder") {
			this.save({ list, of: { ...of, [moving]: target.id } });
		} else if (target.kind === "out") {
			const freed = { ...of };
			delete freed[moving];
			this.save({ list, of: freed });
		} else if (target.id !== moving) {
			const next = { ...of };
			if (of[target.id]) next[moving] = of[target.id];
			else delete next[moving];
			this.save({ list, of: next });
			const to = spaces().find((space) => space.id === target.id);
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

	/** Give a project a browser, or take it away, and let Browsr catch up. */
	toggleBrowser(space) {
		const chosen = { ...this.skipped() };
		if (chosen[space]) delete chosen[space];
		else chosen[space] = true;
		setShared(SKIP, chosen);
	}

	// ---- drawing ------------------------------------------------------

	row(t, space, inFront) {
		const on = !this.skipped()[space.id];
		const letter = (space.name || "?").trim().charAt(0).toUpperCase() || "?";
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
			.on_click(() => {
				// The press that ended a drag is not also a press on a row.
				if (this.dropped) {
					this.dropped = false;
					return;
				}
				run("space.select", { id: space.id });
			})
			// Herdr's own menu, not one of ours: renaming, closing and the rest
			// are its business, and a second menu that did the same things
			// differently would be a worse list, not a better one.
			.on_mouse_down("right", (_event, cx) => {
				cx.stop_propagation();
				openOverlay({
					mod: "menu",
					kind: "popup",
					width: 200,
					height: 260,
					props: { tab: "space", target: { id: space.id } },
				});
			})
			.child(
				h_flex()
					.size(18)
					.flex_none()
					.rounded(5)
					.items_center()
					.justify_center()
					.bg(this.colorOf(space))
					.child(div().text_size(11).text_color("#ffffff").child(letter))
			)
			.child(
				v_flex()
					.flex_1()
					.min_w(0)
					.child(div().truncate().text_color(t.foreground).child(space.name))
					.when(Boolean(space.branch) && !TRUNK.has(space.branch), (el) =>
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
						this.toggleBrowser(space.id);
					})
					.child(svg("mods/powr-spaces/browser.svg").size(13)
						.text_color(on ? t.accent : t.border))
			);
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
				this.menu = this.menu === folder.id ? null : folder.id;
				this.editing = null;
				cx.notify();
			})
			.child(
				h_flex().size(18).flex_none().rounded(5).items_center().justify_center().bg(tint)
					.child(svg("mods/powr-spaces/folder.svg").size(12).text_color("#ffffff"))
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

	folderControls(t, folder) {
		const swatch = (color) =>
			div()
				.id(`powr-paint-${folder.id}-${color ?? "none"}`)
				.size(16)
				.flex_none()
				.rounded(8)
				.cursor_pointer()
				.bg(color ? MARK[color] : t.dim)
				.border_2()
				.border_color((folder.color ?? null) === color ? t.foreground : t.background)
				.on_click((_event, cx) => this.paintFolder(folder.id, color, cx));
		const word = (label, id, handler) =>
			div()
				.id(`powr-folder-${id}-${folder.id}`)
				.px(6)
				.py(2)
				.rounded(4)
				.cursor_pointer()
				.text_size(11)
				.text_color(t.dim)
				.hover((s) => s.bg(t.hover).text_color(t.foreground))
				.on_click(handler)
				.child(label);
		return h_flex()
			.items_center()
			.gap(5)
			.px(8)
			.py(4)
			.child(swatch(null))
			.children(HUES.map(swatch))
			.child(div().flex_1())
			.child(word("rename", "rename", (_event, cx) => {
				this.editing = folder.id;
				this.menu = null;
				this.name.set_value(folder.name);
				this.name.focus();
				cx.notify();
			}))
			.child(word("delete", "delete", (_event, cx) => this.deleteFolder(folder.id, cx)));
	}

	render() {
		const t = theme();
		const here = focused()?.space;
		const { list, of } = this.folders();
		const all = spaces();
		const inFolder = (id) => list.some((f) => f.id === of[id]);

		const body = v_flex().id("powr-list").flex_1().min_h(0).gap(1).px(6).pb(10)
			.overflow_y_scrollbar().mode("always");

		// Folders first, then the projects that are in none. A folder is
		// something you put away; the loose projects are the ones you are
		// actually in, and they belong where the eye lands.
		for (const folder of list) {
			const inside = all.filter((space) => of[space.id] === folder.id);
			body.child(this.folderHead(t, folder, inside));
			if (this.menu === folder.id) body.child(this.folderControls(t, folder));
			if (folder.folded) continue;
			for (const space of inside) {
				body.child(div().pl(14).child(this.row(t, space, space.id === here)));
			}
		}

		// Somewhere to drop a project that is in a folder, to take it out
		// again. Only while one is being dragged, and only while it is in one:
		// otherwise it is a strip of nothing across the list.
		if (this.pressed && of[this.pressed]) {
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

		for (const space of all) {
			if (inFolder(space.id)) continue;
			body.child(this.row(t, space, space.id === here));
		}

		const off = all.filter((space) => this.skipped()[space.id]).length;
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
			.child(body);
	}
}
