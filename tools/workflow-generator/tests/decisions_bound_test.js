// Version-bound approval: the hash of the text the reviewer saw travels from the card (72) or the
// form page (38) to 19, and 19's "changed since you looked" comes back as one clear line per item.
// Runs the Code nodes as they ship in the built workflow.json files.
// Run: node tests/decisions_bound_test.js   (after build.py)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const load = rel => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8'));
const wf38 = load('38-wf-approval-form/workflow.json');
const wf72 = load('72-control-room/n8n/workflow.json');
const js = (wf, name) => { const n = wf.nodes.find(x => x.name === name); assert.ok(n, `${wf.name}: no node ${name}`); return n.parameters.jsCode; };
// nodes: {name: json | [json, ...]} (a list is the node's items, for .all()).
const run = (code, input, nodes, env) => new Function('$input', '$', '$env', code)(
  { first: () => ({ json: input }), all: () => [{ json: input }] },
  name => {
    assert.ok(name in nodes, `unexpected $('${name}')`);
    const list = [].concat(nodes[name]).map(json => ({ json }));
    return { first: () => list[0], all: () => list };
  },
  env);
const one = (...a) => run(...a)[0].json;

const env = { CALENDAR_URL: 'http://cal', LEARNING_URL: 'http://learn', CONTROL_ROOM_KEY: 'k'.repeat(32) };
const H = c => c.repeat(64);
const items = [
  { id: 1, channel: 'linkedin', body: 'Seen text', body_sha256: H('a'), scheduled_at: '2026-10-01T09:00:00Z' },
  { id: 2, channel: 'x', body: 'Old text', body_sha256: H('b'), scheduled_at: '2026-10-01T09:00:00Z' },
  { id: 3, channel: 'x', body: 'Legacy 19, no hash', scheduled_at: '2026-10-01T09:00:00Z' },
];
const opsFor = (out, id) => out.ops.filter(o => o.url.startsWith(`http://cal/items/${id}`));

// 1. Control room (72): approve sends the card's hash; edit PATCHes with if_match and approves the edited text.
const check = one(js(wf72, 'Check request'), { headers: { 'x-control-key': env.CONTROL_ROOM_KEY }, body: { reviewer: 'Sam', decisions: [
  { id: 1, decision: 'approve', seen_sha256: H('c') },       // the card showed an older text than the fetch
  { id: 2, decision: 'edit', text: 'New text\r\n', seen_sha256: H('b') },
  { id: 3, decision: 'approve' },                           // an older control room: no seen hash
] } }, {}, env);
assert.equal(check.ok, true);
assert.deepEqual(check.decisions.map(d => d.seen_sha256), [H('c'), H('b'), '']);
const out72 = one(js(wf72, 'Decisions'), {}, { 'Check request': check, 'Review items': { items } }, env);
const [p1, s1] = opsFor(out72, 1);
assert.deepEqual(p1.body, { scheduled_at: '2026-10-01T09:00:00Z', if_match_sha256: H('c') });
assert.equal(s1.body.status, 'approved');
assert.equal(s1.body.expected_sha256, H('c'), 'the hash the reviewer saw wins over the fetched one');
assert.ok(!('expected_body' in s1.body));
const [p2, s2] = opsFor(out72, 2);
assert.deepEqual([p2.method, p2.body.body, p2.body.if_match_sha256], ['PATCH', 'New text', H('b')]);
assert.equal(s2.body.expected_body, 'New text');
assert.ok(!('expected_sha256' in s2.body));
const [p3, s3] = opsFor(out72, 3);                        // legacy: no hash anywhere, as before
assert.ok(!('if_match_sha256' in p3.body) && !('expected_sha256' in s3.body) && !('expected_body' in s3.body));

// 2. Malformed seen hashes are refused before anything runs.
const chk = seen => one(js(wf72, 'Check request'), { headers: { 'x-control-key': env.CONTROL_ROOM_KEY },
  body: { decisions: [{ id: 1, decision: 'approve', seen_sha256: seen }] } }, {}, env);
for (const bad of ['abc', H('A'), H('g'), 'a'.repeat(65), 5]) assert.equal(chk(bad).status, 422, `seen ${bad}`);
assert.equal(chk(null).ok, true);

// 3. Form 38: no seen field, so the hash from page-render time (the fetched item) is used.
const answers = { 'Decision #1': 'Approve', 'Text #1': 'Seen text', 'Reason #1': '', 'Publish at #1': '',
  'Decision #2': 'Edit & approve', 'Text #2': 'Edited in the form', 'Reason #2': '', 'Publish at #2': '',
  'Decision #3': 'Skip', 'Text #3': '', 'Reason #3': '', 'Publish at #3': '' };
const out38 = one(js(wf38, 'Decisions'), answers, { 'Open approval form': { Reviewer: 'Sam' }, 'Build review page': { items } }, env);
assert.equal(opsFor(out38, 1)[1].body.expected_sha256, H('a'));
assert.equal(opsFor(out38, 2)[0].body.if_match_sha256, H('b'));
assert.equal(opsFor(out38, 2)[1].body.expected_body, 'Edited in the form');
// Approve with the text unchanged in "Edit & approve" is a plain approve, bound by hash.
const same = one(js(wf38, 'Decisions'), { ...answers, 'Decision #1': 'Edit & approve' },
  { 'Open approval form': { Reviewer: 'Sam' }, 'Build review page': { items } }, env);
assert.equal(opsFor(same, 1)[1].body.expected_sha256, H('a'));

// 4. 19 answers 409 "changed since you looked" for item 1 (both stages) and a plain 409 for item 2:
//    item 1 gets one clear line and no learning event; item 2's failure is reported as before.
const d = { ...out72, videos: [] };
const final = one(js(wf72, 'Final decisions'), {}, { 'Decisions': d, 'Video request': [], 'Render video': [] }, env);
const stage = k => final.ops.filter(o => o.stage === k).map(o => ({ ...o, body: JSON.stringify(o.body) }));
const reply = o => o.url === 'http://cal/items/1' || o.url === 'http://cal/items/1/status'
  ? { statusCode: 409, body: { detail: { message: 'changed since you looked', current_sha256: H('d') } } }
  : o.url === 'http://cal/items/2/status'
    ? { statusCode: 409, body: { detail: { message: 'cannot move from draft to approved', current: 'draft' } } }
    : { statusCode: 200, body: {} };
const nodes = { 'Final decisions': final, 'Decisions': d, 'Review items': { items, missing: [] },
  'Stage 1': stage(1), 'Run stage 1': stage(1).map(reply), 'Stage 2': stage(2), 'Run stage 2': stage(2).map(reply) };
const summary = one(js(wf72, 'Summary'), {}, nodes, env);
assert.deepEqual(summary.stale, [1]);
assert.ok(summary.summary.includes('#1: NOT approved: changed since you looked — reopen the card'));
assert.ok(!summary.summary.some(l => /^#1: approved/.test(l)), 'no "approved" line for a refused item');
assert.deepEqual(summary.failed.map(f => [f.item_id, f.status]), [[2, 409]]);
assert.match(summary.message, /#1: NOT approved: changed since you looked/);
const learn = run(js(wf72, 'Learning events'), {}, nodes, env).map(i => JSON.parse(i.json.body).item_id);
assert.deepEqual(learn, [2, 3]);
const result = one(js(wf72, 'Result'), {}, { ...nodes, 'Summary': summary }, env);
assert.equal(result.ok, false);
assert.deepEqual(result.stale, [1]);
// 428 (a bound item approved without a hash) is the same line.
const r428 = one(js(wf72, 'Summary'), {}, { ...nodes, 'Run stage 1': stage(1).map(() => ({ statusCode: 200, body: {} })),
  'Run stage 2': stage(2).map(o => o.url.includes('/items/3/') ? { statusCode: 428, body: { detail: 'needs a bound approval' } } : { statusCode: 200, body: {} }) }, env);
assert.deepEqual([r428.stale, r428.failed], [[3], []]);
// Everything accepted: ok, no stale lines.
const fine = one(js(wf72, 'Summary'), {}, { ...nodes, 'Run stage 1': stage(1).map(() => ({ statusCode: 200 })),
  'Run stage 2': stage(2).map(() => ({ statusCode: 200 })) }, env);
assert.deepEqual([fine.stale, fine.failed, fine.summary], [[], [], out72.summary]);

// 5. An edited video script: the approve after the re-render carries the edited text too.
const vitem = { id: 6, channel: 'video', body: 'Hook: Old\n\nSay: Beat\n\nClose: End', body_sha256: H('e'), video_url: 'http://video/videos/' + 'a'.repeat(32) + '.mp4' };
const vcheck = one(js(wf72, 'Check request'), { headers: { 'x-control-key': env.CONTROL_ROOM_KEY }, body: { decisions: [
  { id: 6, decision: 'edit', text: 'Hook: New\n\nSay: Beat\n\nClose: End', seen_sha256: H('e') }] } }, {}, env);
const vd = one(js(wf72, 'Decisions'), {}, { 'Check request': vcheck, 'Review items': { items: [vitem] } }, { ...env, VIDEO_URL: 'http://video' });
assert.equal(vd.videos.length, 1);
assert.deepEqual(vd.videos[0].bound, { expected_body: 'Hook: New\n\nSay: Beat\n\nClose: End' });
assert.equal(vd.videos[0].patch.if_match_sha256, H('e'));
console.log('decisions bound to the seen text (ok)');
