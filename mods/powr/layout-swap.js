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
