import assert from 'node:assert/strict';
import { test } from 'node:test';
import { swapMod } from '../mods/powr/layout-swap.js';

const tree = {
	type: 'column', children: [
		{ id: 'spaces', type: 'block', content: { mod: 'spacr' }, size: { weight: 1 } },
		{ id: 'agents', type: 'block', content: { mod: 'agents' } },
		{ id: 'row', type: 'row', children: [
			{ id: 'b', type: 'block', content: { mod: 'powr', props: { role: 'button' } } },
		] },
	],
};

test('the list block changes mod, nothing else does', () => {
	const { tree: out, changed } = swapMod(tree, 'spacr', 'spaces');
	assert.equal(changed, 1);
	assert.equal(out.children[0].content.mod, 'spaces');
	assert.deepEqual(out.children[0].size, { weight: 1 });
	assert.equal(out.children[1].content.mod, 'agents');
	assert.deepEqual(out.children[2].children[0].content, { mod: 'powr', props: { role: 'button' } });
});

test('the original tree is left as it was', () => {
	swapMod(tree, 'spacr', 'spaces');
	assert.equal(tree.children[0].content.mod, 'spacr');
});

test('a layout without the list reports no change', () => {
	const { changed } = swapMod({ type: 'block', content: { tab: 'active' } }, 'spacr', 'spaces');
	assert.equal(changed, 0);
});
