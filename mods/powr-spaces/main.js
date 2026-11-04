import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
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
const COLORS = "powr.space-colors-herdr";
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

/** A project with no colour of its own still gets a steady one. */
function hue(id) {
	let sum = 0;
	for (let i = 0; i < id.length; i += 1) sum = (sum * 31 + id.charCodeAt(i)) >>> 0;
	return HUES[sum % HUES.length];
}

// Branches worth printing under a name. Almost every project sits on its
// trunk, so printing it on every row costs a second line each and tells you
// nothing; a branch is news only when it is not the trunk.
const TRUNK = new Set(["main", "master", "trunk", "develop"]);

/** A project speaks up only when something in it needs you. */
function glyph(status) {
	return status === "blocked" || status === "done" ? "●" : "·";
}

export default class PowrSpaces extends View {
	init(_props, cx) {
		this.seedFolders();
		cx.notify();
	}

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

	skipped() {
		const chosen = shared(SKIP);
		return chosen && typeof chosen === "object" ? chosen : {};
	}

	colorOf(space) {
		const stored = shared(HERDR_COLORS) ?? shared(COLORS);
		const named = stored && typeof stored === "object" ? stored[space.id] : null;
		return MARK[named] ?? MARK[hue(space.id)];
	}

	toggleFolder(id) {
		const { list, of } = this.folders();
		setShared(FOLDERS, {
			of,
			list: list.map((f) => (f.id === id ? { ...f, folded: !f.folded } : f)),
		});
	}

	/** Give a project a browser, or take it away, and let Browsr catch up. */
	toggleBrowser(space) {
		const chosen = { ...this.skipped() };
		if (chosen[space]) delete chosen[space];
		else chosen[space] = true;
		setShared(SKIP, chosen);
	}

	row(t, space, inFront) {
		const on = !this.skipped()[space.id];
		const letter = (space.name || "?").trim().charAt(0).toUpperCase() || "?";
		return h_flex()
			.id(`powr-space-${space.id}`)
			.items_center()
			.gap(8)
			.px(8)
			.py(4)
			.rounded(6)
			.cursor_pointer()
			.when(inFront, (el) => el.bg(t.selected))
			.hover((s) => s.bg(t.hover))
			.on_click(() => run("space.select", { id: space.id }))
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
		return h_flex()
			.id(`powr-folder-${folder.id}`)
			.items_center()
			.gap(8)
			.px(8)
			.py(4)
			.rounded(6)
			.cursor_pointer()
			.hover((s) => s.bg(t.hover))
			.on_click(() => this.toggleFolder(folder.id))
			.child(
				h_flex().size(18).flex_none().rounded(5).items_center().justify_center().bg(tint)
					.child(svg("mods/powr-spaces/folder.svg").size(12).text_color("#ffffff"))
			)
			.child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(folder.name))
			.child(div().flex_none().text_size(11).text_color(t.dim).child(String(inside.length)))
			.child(svg(`mods/powr-spaces/chevron-${folder.folded ? "right" : "down"}.svg`)
				.size(11).flex_none().text_color(t.dim));
	}

	render() {
		const t = theme();
		const here = focused()?.space;
		const { list, of } = this.folders();
		const all = spaces();
		const inFolder = (id) => list.some((f) => f.id === of[id]);

		// Folders first and closed, then the projects that are in none. A
		// folder is something you put away; the loose projects are the ones
		// you are actually in, and they belong where the eye lands.
		const body = v_flex().id("powr-list").flex_1().min_h(0).gap(1).px(6).pb(10)
			.overflow_y_scrollbar().mode("always");
		for (const folder of list) {
			const inside = all.filter((space) => of[space.id] === folder.id);
			body.child(this.folderHead(t, folder, inside));
			if (folder.folded) continue;
			for (const space of inside) {
				body.child(div().pl(14).child(this.row(t, space, space.id === here)));
			}
		}
		for (const space of all) {
			if (inFolder(space.id)) continue;
			body.child(this.row(t, space, space.id === here));
		}

		const off = all.filter((space) => this.skipped()[space.id]).length;
		return v_flex()
			.id("root")
			.size_full()
			.bg(t.background)
			.py(8)
			.gap(6)
			.text_size(12.5)
			.child(
				h_flex().px(14).items_center().gap(6)
					.child(div().flex_1().text_size(11).text_color(t.dim).child("spaces"))
					.when(off > 0, (el) => el.child(
						div().text_size(10.5).text_color(t.dim).child(`${off} without a browser`)))
					.child(
						div().id("powr-new-space").px(5).rounded(5).text_color(t.dim)
							.cursor_pointer()
							.tooltip("New space")
							.hover((s) => s.bg(t.hover).text_color(t.foreground))
							.on_click(() => run("space.new"))
							.child("+")
					)
			)
			.child(body);
	}
}
