import { shared, openOverlay } from "herdr";

// The account an agent runs on, for the badge in the Agents list. Agents on the
// main account have no entry, so they show nothing.
export function switchrBadge(agent) {
	const where = shared("switchr.where");
	const name = where && where[agent.address];
	return typeof name === "string" && name ? name : null;
}

// Right-click on an agent row: the account popup (drawn by the powr mod).
export function switchrMenu(agent) {
	openOverlay({
		mod: "powr", kind: "popup", width: 240, height: 220,
		props: { role: "account", address: agent.address },
	});
}
