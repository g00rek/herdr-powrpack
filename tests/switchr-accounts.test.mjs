import assert from 'node:assert/strict';
import { test } from 'node:test';
import { usageLine, withUsage, UNKNOWN, COLORS, paint } from '../mods/powr/switchr-accounts.js';

const ok = { session: 12, week: 40, resets: null, state: 'ok' };

test('names-only rows say the limits are still coming', () => {
	assert.equal(usageLine({ kind: 'claude', name: 'work', usage: null }), 'Checking limits…');
	assert.equal(usageLine({ kind: 'claude', name: 'work', pending: true, usage: null }), 'Not logged in');
	assert.equal(usageLine({ usage: ok }), '5 h 12% · week 40%');
	assert.equal(usageLine({ usage: UNKNOWN }), 'limit unknown');
	assert.equal(usageLine({ usage: { state: 'logged-out' } }), 'Not logged in');
});

test('limits that arrive are laid over the names', () => {
	const names = [{ kind: 'claude', name: 'main', usage: null }, { kind: 'codex', name: 'main', usage: null }];
	const full = [{ kind: 'claude', name: 'main', usage: ok }];
	assert.deepEqual(withUsage(names, full, UNKNOWN).map((a) => a.usage), [ok, UNKNOWN]);
});

test('a failed limits call leaves every name in place', () => {
	const names = [{ kind: 'claude', name: 'main', usage: null }];
	const rows = withUsage(names, undefined, UNKNOWN);
	assert.equal(rows.length, 1);
	assert.equal(usageLine(rows[0]), 'limit unknown');
});

test('a fresh names list keeps the limits already known', () => {
	const before = [{ kind: 'claude', name: 'work', usage: ok }];
	const names = [{ kind: 'claude', name: 'work', usage: null }, { kind: 'claude', name: 'new', usage: null }];
	assert.deepEqual(withUsage(names, before, null).map((a) => a.usage), [ok, null]);
});

test('a colour is painted from the theme, the accent when none', () => {
	const t = { accent: '#a', ansi1: '#r', ansi4: '#b', ansi6: '#c' };
	assert.equal(paint(t, 'red'), '#r');
	assert.equal(paint(t, 'blue'), '#b');
	assert.equal(paint(t, 'cyan'), '#c');
	assert.equal(paint(t, null), '#a');
	assert.equal(paint(t, 'pink'), '#a');
	assert.equal(COLORS[0], null);
});
