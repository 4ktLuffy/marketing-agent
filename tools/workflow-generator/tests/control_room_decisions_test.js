// The control room's webhook (72-control-room/n8n/workflow.json) must decide exactly like the
// approval form (38). Runs the Code nodes as they ship in both built workflow.json files.
// Run: node tests/control_room_decisions_test.js   (after build.py)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const load = rel => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
const wf38 = load('38-wf-approval-form/workflow.json');
const wf72 = load('72-control-room/n8n/workflow.json');
const node = (wf, name) => { const n = wf.nodes.find(x => x.name === name); assert.ok(n, `${wf.name}: no node ${name}`); return n; };
const run = (code, input, nodes, env) => new Function('$input', '$', '$env', code)(
  { first: () => ({ json: input }), all: () => [{ json: input }] },
  name => { assert.ok(name in nodes, `unexpected $('${name}')`); return { first: () => ({ json: nodes[name] }), all: () => [{ json: nodes[name] }] }; },
  env)[0].json;

// 1. Same decision code: 72's "Decisions" ends with 38's code after 38's form adapter.
const d38 = node(wf38, 'Decisions').parameters.jsCode;
const d72 = node(wf72, 'Decisions').parameters.jsCode;
const core = d38.slice(d38.indexOf('const cal = $env.CALENDAR_URL;'));
assert.ok(core.length > 1000 && d72.endsWith(core), 'the decision code differs from the form');
// Every node after "Decisions" is the same, except which node holds the items.
for (const n of wf38.nodes.filter(x => ['Video request', 'Final decisions', 'Calendar ops', 'Stage 1', 'Stage 2',
  'Learning events', 'Rewrites', 'Summary', 'Engine outcomes', 'Run stage 1', 'Run stage 2', 'Render video'].includes(x.name))) {
  const m = node(wf72, n.name);
  const p38 = JSON.stringify(n.parameters).replace("$('Build review page')", "$('Review items')");
  assert.equal(JSON.stringify(m.parameters), p38, `node ${n.name} differs`);
}
assert.deepEqual(wf72.connections['Start rewrites'], wf38.connections['Start rewrites']);

// 2. Same result for the same decisions.
const KEY = 'k'.repeat(32);
const env = { CALENDAR_URL: 'http://cal', VIDEO_URL: 'http://video', CONTROL_ROOM_KEY: KEY };
const items = [
  { id: 1, channel: 'linkedin', body: 'Old text', scheduled_at: '2026-10-01T09:00:00Z', campaign_id: 3, notes: 'engine pillar #2 slot #5' },
  { id: 2, channel: 'x', body: 'X post', scheduled_at: null },
  { id: 3, channel: 'instagram', body: 'IG post', scheduled_at: '2026-10-02T10:00:00Z' },
  { id: 4, channel: 'x', body: 'Drop me', scheduled_at: null },
  { id: 5, channel: 'linkedin', body: 'Back please', scheduled_at: null },
  { id: 6, channel: 'video', body: 'Hook: Cold brew\nOn screen: COLD\n\nSay: Steep it\nOn screen: STEEP\n\nClose: Try it', video_url: 'http://video/videos/' + 'a'.repeat(32) + '.mp4', image_url: 'http://video/videos/' + 'a'.repeat(32) + '.jpg' },
  { id: 7, channel: 'x', body: 'Untouched' },
  // A clip from 73 (tool 77): editing its caption must not re-render (or drop) the video.
  { id: 8, channel: 'video', body: 'Clip caption', video_url: 'http://localhost:8173/clips/' + 'b'.repeat(32) + '.mp4', image_url: 'http://localhost:8173/clips/' + 'b'.repeat(32) + '.jpg' },
];
const decisions = [
  { id: 1, decision: 'edit', text: 'New text', reason: 'shorter', publish_at: '2026-10-03 08:30' },
  { id: 2, decision: 'approve' },
  { id: 3, decision: 'reject_rewrite', reason: 'too salesy' },
  { id: 4, decision: 'reject_drop' },
  { id: 5, decision: 'back_to_draft', reason: 'later' },
  { id: 6, decision: 'edit', text: 'Hook: Cold brew now\nOn screen: COLD\n\nSay: Steep it\nOn screen: STEEP\n\nClose: Try it' },
  { id: 7, decision: 'skip' },
  { id: 8, decision: 'edit', text: 'Clip caption, edited' },
];
const LABELS = { approve: 'Approve', edit: 'Edit & approve', reject_rewrite: 'Reject – rewrite it',
  reject_drop: 'Reject – drop it', back_to_draft: 'Back to draft', skip: 'Skip' };
const answers = {};
for (const it of items) {  // the form sends every field; Text defaults to the body
  const x = decisions.find(d => d.id === it.id);
  answers[`Decision #${it.id}`] = LABELS[x.decision];
  answers[`Text #${it.id}`] = x.text ?? it.body;
  answers[`Reason #${it.id}`] = x.reason ?? '';
  answers[`Publish at #${it.id}`] = x.publish_at ?? '';
}
const out38 = run(d38, answers, { 'Open approval form': { Reviewer: 'Sam' }, 'Build review page': { items } }, env);

const check = run(node(wf72, 'Check request').parameters.jsCode,
  { headers: { 'x-control-key': KEY }, body: { reviewer: 'Sam', decisions } }, {}, env);
assert.equal(check.ok, true);
const review = run(node(wf72, 'Review items').parameters.jsCode,
  { statusCode: 200, body: [...items, { id: 99, channel: 'x', body: 'not asked about' }] }, { 'Check request': check }, env);
assert.deepEqual(review.missing, []);
assert.deepEqual(review.items.map(i => i.id), [1, 2, 3, 4, 5, 6, 8]);
const out72 = run(d72, {}, { 'Check request': check, 'Review items': review }, env);
assert.deepEqual(out72, out38);
assert.equal(out72.videos.length, 1);                     // 6: re-rendered before approving
assert.deepEqual(out72.revisions, [{ item_id: '3', reason: 'too salesy' }]);
assert.deepEqual(out72.events.map(e => [e.item_id, e.decision]), [[1, 'edited'], [2, 'approved'], [3, 'rejected'], [4, 'rejected'], [8, 'edited']]);
// 8 (a clip): the edited caption is saved and approved; the clip's video stays.
const clipOps = out72.ops.filter(o => o.url === 'http://cal/items/8' || o.url === 'http://cal/items/8/status');
assert.deepEqual(clipOps.map(o => [o.stage, o.method]), [[1, 'PATCH'], [2, 'POST']]);
assert.equal(clipOps[0].body.body, 'Clip caption, edited');
assert.ok(!('video_url' in clipOps[0].body));
assert.equal(clipOps[1].body.status, 'approved');

// 3. The key check: missing, wrong, unset in n8n -> 401; bad body -> 422; stale item -> reported.
const chk = (headers, body, e = env) => run(node(wf72, 'Check request').parameters.jsCode, { headers, body }, {}, e);
assert.equal(chk({}, { decisions }).status, 401);
assert.equal(chk({ 'x-control-key': KEY + 'x' }, { decisions }).status, 401);
assert.equal(chk({ 'x-control-key': 'k'.repeat(31) }, { decisions }).status, 401);
assert.equal(chk({ 'x-control-key': '' }, { decisions }, { ...env, CONTROL_ROOM_KEY: '' }).status, 401);
assert.equal(chk({ 'x-control-key': KEY }, { decisions: [{ id: 1, decision: 'publish' }] }).status, 422);
assert.equal(chk({ 'x-control-key': KEY }, { decisions: [] }).status, 422);
assert.equal(chk({ 'x-control-key': KEY }, { decisions: [{ id: '1', decision: 'approve' }] }).status, 422);
const stale = run(node(wf72, 'Review items').parameters.jsCode, { body: [items[1]] },
  { 'Check request': chk({ 'x-control-key': KEY }, { decisions: [{ id: 2, decision: 'approve' }, { id: 50, decision: 'approve' }] }) }, env);
assert.deepEqual([stale.count, stale.missing], [1, [50]]);
console.log('control room decisions: same as form 38 (ok)');
