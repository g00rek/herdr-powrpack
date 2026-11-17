import { shared, openOverlay } from "herdr";

// The account an agent runs on, for the badge in the Agents list. Every agent
// shows its account; none is treated as the usual one.
export function switchrBadge(agent) {
	const where = shared("switchr.where");
	const name = where && where[agent.address];
	return typeof name === "string" && name ? name : null;
}

// The badge's colour: the one picked for the account, as the theme paints it,
// or the accent when none was picked.
const ANSI = { red: "ansi1", green: "ansi2", yellow: "ansi3", blue: "ansi4", magenta: "ansi5", cyan: "ansi6" };
export function switchrColor(agent, t) {
	const colors = shared("switchr.color");
	const slot = ANSI[colors && colors[agent.address]];
	return (slot && t[slot]) || t.accent;
}

// Right-click on an agent row: the account popup (drawn by the powr mod).
export function switchrMenu(agent) {
	openOverlay({
		mod: "powr", kind: "popup", width: 240, height: 220,
		props: { role: "account", address: agent.address },
	});
}
