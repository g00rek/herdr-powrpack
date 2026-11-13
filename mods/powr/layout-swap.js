// Swapping which mod a layout block shows, as a pure function so it can be
// tested outside Herdr. Used to put Herdr's own project list back when Spacr
// is switched off, and Spacr back when it is switched on.

export function swapMod(tree, from, to) {
	let changed = 0;
	const walk = (node) => {
		if (Array.isArray(node)) return node.map(walk);
		if (!node || typeof node !== 'object') return node;
		const copy = {};
		for (const [key, value] of Object.entries(node)) copy[key] = walk(value);
		if (copy.type === 'block' && copy.content && copy.content.mod === from) {
			copy.content = { ...copy.content, mod: to };
			changed += 1;
		}
		return copy;
	};
	return { tree: walk(tree), changed };
}

// The sizes a person dragged, from the app's click file for a layout
// (layouts/.state/<name>.json: { nodes: { [id]: { size: { px } | { weight } } } }).
// layout.set forgets them, so they are put back with layout.resize after it.
// The swapped block keeps its id, so its size comes back with it.
export function savedSizes(stateText) {
	let state;
	try {
		state = JSON.parse(stateText);
	} catch {
		return [];
	}
	const nodes = state && typeof state === 'object' ? state.nodes : null;
	if (!nodes || typeof nodes !== 'object') return [];
	const out = [];
	for (const [id, node] of Object.entries(nodes)) {
		const size = node && node.size;
		if (!size || typeof size !== 'object') continue;
		if (typeof size.px === 'number') out.push({ id, px: size.px });
		else if (typeof size.weight === 'number') out.push({ id, weight: size.weight });
	}
	return out;
}
