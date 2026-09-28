// node tests/activity_js_test.js — the Activity page's pure helpers (app/static/activity.js).
'use strict';
const assert = require('assert');
const path = require('path');
const H = require(path.join(__dirname, '..', 'app', 'static', 'activity.js'));

assert.strictEqual(H.seconds(14200), '14.2 s');
assert.strictEqual(H.seconds(0), '0.0 s');
assert.strictEqual(H.seconds(125000), '2 min 5 s');
assert.strictEqual(H.seconds(null), '–');
assert.strictEqual(H.ticking(9999), '9 s');
assert.strictEqual(H.ticking(61000), '1 min 01 s');
assert.strictEqual(H.ticking(-5), '0 s');

const now = new Date(2026, 8, 28, 15, 0, 0);
assert.strictEqual(H.dayLabel(new Date(2026, 8, 28, 1, 0), now), 'Today');
assert.strictEqual(H.dayLabel(new Date(2026, 8, 27, 23, 59), now), 'Yesterday');
assert.notStrictEqual(H.dayLabel(new Date(2026, 8, 20, 12, 0), now), 'Yesterday');

const ev = [
  { id: 'a', at: new Date(2026, 8, 28, 14, 0).toISOString(), kind: 'ai', ok: true, ability: 26 },
  { id: 'b', at: new Date(2026, 8, 28, 13, 0).toISOString(), kind: 'ai', ok: false, ability: 44 },
  { id: 'c', at: new Date(2026, 8, 27, 9, 0).toISOString(), kind: 'content', ok: true, ability: null },
  { id: 'd', at: 'garbage', kind: 'content', ok: false, ability: null },
];
const g = H.groupByDay(ev, now);
assert.deepStrictEqual(g.map(x => [x.label, x.events.map(e => e.id)]),
  [['Today', ['a', 'b']], ['Yesterday', ['c']], ['Unknown day', ['d']]]);
assert.deepStrictEqual(H.filterEvents(ev, 'all', null).map(e => e.id), ['a', 'b', 'c', 'd']);
assert.deepStrictEqual(H.filterEvents(ev, 'ai', null).map(e => e.id), ['a', 'b']);
assert.deepStrictEqual(H.filterEvents(ev, 'content', null).map(e => e.id), ['c', 'd']);
assert.deepStrictEqual(H.filterEvents(ev, 'errors', null).map(e => e.id), ['b']);   // a rejected item is not an error
assert.deepStrictEqual(H.filterEvents(ev, 'all', 26).map(e => e.id), ['a']);
assert.deepStrictEqual(H.filterEvents(ev, 'errors', 26), []);

assert.strictEqual(H.sparkPoints([]), '');
assert.strictEqual(H.sparkPoints([5]), '');
assert.strictEqual(H.sparkPoints([0, 10]), '0.0,26.0 120.0,2.0');
assert.strictEqual(H.sparkPoints([10, 'x', 10, NaN]), '0.0,2.0 120.0,2.0');
assert.strictEqual(H.sparkPoints([0, 0, 0]).split(' ').length, 3);   // all zero: flat, no NaN
assert.ok(!H.sparkPoints([0, 0]).includes('NaN'));

assert.strictEqual(H.ago(new Date(now - 20e3).toISOString(), now), 'just now');
assert.strictEqual(H.ago(new Date(now - 5 * 60e3).toISOString(), now), '5 min ago');
assert.strictEqual(H.ago(new Date(now - 3 * 3600e3).toISOString(), now), '3 h ago');
assert.strictEqual(H.ago('', now), '');
console.log('activity_js_test: ok');
