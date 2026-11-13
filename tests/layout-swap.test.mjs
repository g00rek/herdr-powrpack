import assert from 'node:assert/strict';
import { test } from 'node:test';
import { swapMod, savedSizes } from '../mods/powr/layout-swap.js';

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

test('switchr swaps only the agents block, and switching back restores it', () => {
	const { tree: on, changed } = swapMod(tree, 'agents', 'switchr-agents');
	assert.equal(changed, 1);
	assert.equal(on.children[1].content.mod, 'switchr-agents');
	assert.equal(on.children[0].content.mod, 'spacr');
	assert.deepEqual(on.children[2], tree.children[2]);
	const { tree: off, changed: back } = swapMod(on, 'switchr-agents', 'agents');
	assert.equal(back, 1);
	assert.deepEqual(off, tree);
});

test('saved sizes: every dragged px or weight, nothing else', () => {
	const text = JSON.stringify({
		nodes: {
			agents: { size: { weight: 0.3385461 } },
			spaces: { size: { weight: 1.6614538 } },
			taskbar: { size: { px: 21.538 } },
			main: { pane: { header_height: 13.8 } },
		},
		file: { id: 'r', type: 'column' },
	});
	assert.deepEqual(savedSizes(text), [
		{ id: 'agents', weight: 0.3385461 },
		{ id: 'spaces', weight: 1.6614538 },
		{ id: 'taskbar', px: 21.538 },
	]);
});

test('saved sizes: no file, bad JSON or no nodes gives nothing', () => {
	assert.deepEqual(savedSizes(''), []);
	assert.deepEqual(savedSizes('{not json'), []);
	assert.deepEqual(savedSizes('null'), []);
	assert.deepEqual(savedSizes('{"file":{}}'), []);
});
