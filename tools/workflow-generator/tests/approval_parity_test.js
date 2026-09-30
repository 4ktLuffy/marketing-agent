// The approval service (90-approval-service) must decide exactly like n8n's decision webhook
// (72-control-room/n8n/workflow.json, the same DECISIONS_CORE as form 38), except for the
// differences listed in DIFFERENCES below. Runs the Code nodes as they ship in the built
// workflow.json and the service's pure planner (90-approval-service/app/planner.py, run with
// $PYTHON or python3) on the same cases, and compares the calendar operations (method, path,
// body), summaries, learning events, engine outcomes, request checks and the final answer.
// Run: node tests/approval_parity_test.js   (after build.py; needs python3 3.9+)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '../../..');
const SERVICE = path.join(ROOT, '90-approval-service');
if (!fs.existsSync(path.join(SERVICE, 'app', 'planner.py'))) {
  console.log('approval parity: skipped (90-approval-service is not next to the generator)');
  process.exit(0);
}
const wf72 = JSON.parse(fs.readFileSync(path.join(ROOT, '72-control-room/n8n/workflow.json'), 'utf8'));
const js = name => { const n = wf72.nodes.find(x => x.name === name); assert.ok(n, `no node ${name}`); return n.parameters.jsCode; };
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

// VIDEO_URL is unset: n8n cannot re-render either, so its edited-video path is the "could not be
// re-rendered, back to draft" one, which is what the service always does.
const CAL = 'http://cal';
const KEY = 'k'.repeat(32);
const env = { CALENDAR_URL: CAL, LEARNING_URL: 'http://learn', ENGINE_URL: 'http://engine', CONTROL_ROOM_KEY: KEY };
const H = c => c.repeat(64);
const V = c => `http://video.test/videos/${c.repeat(32)}.mp4`;

const ITEMS = [
  { id: 1, channel: 'linkedin', body: 'Seen text', body_sha256: H('a'), scheduled_at: '2026-10-01T09:00:00Z', campaign_id: 3, notes: 'engine pillar #2 slot #5' },
  { id: 2, channel: 'x', body: 'Legacy 19, no hash', scheduled_at: null },
  { id: 3, channel: 'instagram', body: 'IG post\r\n', body_sha256: H('b'), scheduled_at: '2026-10-02T10:00:00Z' },
  { id: 4, channel: 'x', body: 'Drop me', body_sha256: H('c'), scheduled_at: null },
  { id: 5, channel: 'linkedin', body: 'Back please', body_sha256: H('d'), scheduled_at: null, notes: 'engine pillar #7' },
  { id: 6, channel: 'video', body: 'Hook: Old\n\nSay: Beat\n\nClose: End', body_sha256: H('e'), video_url: V('a'), image_url: V('a').replace(/\.mp4$/, '.jpg') },
  { id: 7, channel: 'video', body: 'Clip caption', body_sha256: H('f'), video_url: 'http://localhost:8173/clips/' + 'b'.repeat(32) + '.mp4' },
  { id: 8, channel: 'Video', body: 'Hook: No video yet', body_sha256: H('1'), video_url: null },
  { id: 9, channel: 'tiktok', body: 'Own image', body_sha256: H('2'), video_url: V('c'), image_url: 'http://cards.test/c.png' },
];
const T = (decision, extra = {}) => ({ decision, ...extra });

// [name, reviewer, decisions (ids refer to ITEMS; 99 is not in review), difference?]
const CASES = [
  ['approve', 'Sam', [{ id: 1, ...T('approve') }]],
  ['approve with the seen hash', 'Sam', [{ id: 1, ...T('approve', { seen_sha256: H('9') }) }]],
  ['approve, no hash anywhere, no scheduled_at', 'Sam', [{ id: 2, ...T('approve') }]],
  ['approve with publish_at (naive)', 'Sam', [{ id: 1, ...T('approve', { publish_at: '2026-10-03 08:30' }) }]],
  ['approve with publish_at (offset)', 'Sam', [{ id: 4, ...T('approve', { publish_at: '2026-10-03T08:30:00+02:00' }) }]],
  ['approve with publish_at (date only)', 'Sam', [{ id: 4, ...T('approve', { publish_at: '2026-10-03' }) }]],
  ['approve with an unreadable publish_at', 'Sam', [{ id: 1, ...T('approve', { publish_at: 'tomorrow' }) }]],
  ['approve with a reason', 'Sam Jones', [{ id: 1, ...T('approve', { reason: 'great hook' }) }]],
  ['edit', 'Sam', [{ id: 3, ...T('edit', { text: 'New IG text\r\n', reason: 'shorter', seen_sha256: H('b') }) }]],
  ['edit, no scheduled_at, no seen hash', 'Sam', [{ id: 4, ...T('edit', { text: 'Dropped? No, edited.' }) }]],
  ['edit with the text unchanged', 'Sam', [{ id: 3, ...T('edit', { text: '  IG post\r\n' }) }]],
  ['edit with no text', 'Sam', [{ id: 1, ...T('edit') }]],
  ['edit a clip caption', 'Sam', [{ id: 7, ...T('edit', { text: 'Clip caption, edited' }) }]],
  ['reject_drop', 'Sam', [{ id: 4, ...T('reject_drop', { reason: 'off brand' }) }]],
  ['reject_rewrite without a reason', 'Sam', [{ id: 4, ...T('reject_rewrite') }]],
  ['reject_rewrite with a reason', 'Sam', [{ id: 3, ...T('reject_rewrite', { reason: 'too salesy' }) }], 'rewrite'],
  ['back_to_draft', 'Sam', [{ id: 5, ...T('back_to_draft', { reason: 'later' }) }]],
  ['skip', 'Sam', [{ id: 1, ...T('skip') }, { id: 2, ...T('approve') }]],
  ['not in review', 'Sam', [{ id: 99, ...T('approve') }, { id: 2, ...T('approve') }]],
  ['nothing in review', 'Sam', [{ id: 99, ...T('approve') }]],
  ['the last decision on an id wins', 'Sam', [{ id: 4, ...T('approve') }, { id: 4, ...T('reject_drop') }]],
  ['blank reviewer', '  ', [{ id: 1, ...T('approve') }]],
  ['edited video (rendered)', 'Sam', [{ id: 6, ...T('edit', { text: 'Hook: New\n\nSay: Beat\n\nClose: End', seen_sha256: H('e') }) }], 'video'],
  ['edited video, own image kept', 'Sam', [{ id: 9, ...T('edit', { text: 'New words' }) }], 'video'],
  ['edited video-channel item without a video', 'Sam', [{ id: 8, ...T('edit', { text: 'Hook: Now with words' }) }], 'video-channel'],
  ['everything at once', 'Sam', [
    { id: 5, ...T('back_to_draft') }, { id: 1, ...T('approve', { seen_sha256: H('a') }) },
    { id: 3, ...T('edit', { text: 'Edited' }) }, { id: 4, ...T('reject_rewrite') }, { id: 7, ...T('skip') },
    { id: 2, ...T('approve', { publish_at: '2026-11-01T12:00Z' }) }, { id: 99, ...T('reject_drop') }]],
];

// Requests n8n refuses, each with the message the service must give too.
const long = n => 'x'.repeat(n);
const BAD = [
  { decisions: [] }, { decisions: Array.from({ length: 51 }, (_, i) => ({ id: i + 1, decision: 'skip' })) },
  { decisions: 'approve all' }, {}, { decisions: [null] }, { decisions: [5] }, { decisions: [[1]] },
  { decisions: [{ id: '1', decision: 'approve' }] }, { decisions: [{ id: 0, decision: 'approve' }] },
  { decisions: [{ id: 1.5, decision: 'approve' }] }, { decisions: [{ id: true, decision: 'approve' }] },
  { decisions: [{ id: 1, decision: 'publish' }] }, { decisions: [{ id: 1, decision: 'approve', text: long(60001) }] },
  { decisions: [{ id: 1, decision: 'approve', reason: long(501) }] }, { decisions: [{ id: 1, decision: 'approve', publish_at: long(41) }] },
  { decisions: [{ id: 1, decision: 'approve', reason: 5 }] },
  { decisions: [{ id: 1, decision: 'approve', seen_sha256: 'abc' }] }, { decisions: [{ id: 1, decision: 'approve', seen_sha256: H('A') }] },
  { decisions: [{ id: 1, decision: 'approve', seen_sha256: 5 }] }, { decisions: [{ id: 1, decision: 'approve', seen_sha256: H('g') }] },
  { reviewer: long(81), decisions: [{ id: 1, decision: 'approve' }] }, { reviewer: 5, decisions: [{ id: 1, decision: 'approve' }] },
  // Accepted by both:
  { reviewer: null, decisions: [{ id: 1.0, decision: 'approve', seen_sha256: null, text: long(60000) }] },
  { decisions: Array.from({ length: 50 }, (_, i) => ({ id: i + 1, decision: 'skip' })) },
];

// ---------- n8n: Check request -> Review items -> Decisions -> Final decisions
const now = Date.now();
function n8n(reviewer, decisions) {
  const check = one(js('Check request'), { headers: { 'x-control-key': KEY }, body: { reviewer, decisions } }, {}, env);
  assert.equal(check.ok, true, JSON.stringify(check));
  const review = one(js('Review items'), { statusCode: 200, body: ITEMS }, { 'Check request': check }, env);
  if (review.count === 0) return { review, nothing: true };
  const d = one(js('Decisions'), {}, { 'Check request': check, 'Review items': review }, env);
  const final = one(js('Final decisions'), {}, { 'Decisions': d, 'Video request': [], 'Render video': [] }, env);
  return { check, review, d, final };
}
const opsOf = ops => ops.map(o => ({ stage: o.stage, method: o.method, path: o.url.slice(CAL.length), body: o.body }));

// ---------- the service's planner, in one python run
const PY = String.raw`
import json, sys
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
from app import planner
req = json.load(sys.stdin)
now = datetime.fromtimestamp(req["now"] / 1000, timezone.utc)
out = {"cases": [], "bad": [], "results": []}
for c in req["cases"]:
    reviewer, ds = planner.validate({"reviewer": c["reviewer"], "decisions": c["decisions"]})
    p = planner.plan(ds, req["items"], reviewer, now)
    for o in p["ops"]:
        del o["item_id"]
    out["cases"].append(p)
for b in req["bad"]:
    try:
        planner.validate(b)
        out["bad"].append(None)
    except planner.Invalid as e:
        out["bad"].append(str(e))
for r in req["results"]:
    reviewer, ds = planner.validate({"reviewer": r["reviewer"], "decisions": r["decisions"]})
    p = planner.plan(ds, req["items"], reviewer, now)
    out["results"].append(planner.result(p, r["failures"]))
print(json.dumps(out))
`;

// ---------- failures: what 19 answers, stage by stage (n8n runs every op of both stages)
const FAILS = [
  ['all accepted', () => ({ statusCode: 200, body: {} })],
  ['item 1 changed since you looked (both stages), item 3 a plain 409, item 2 a 428', o =>
    /\/items\/1(\/|$)/.test(o.url) ? { statusCode: 409, body: { detail: { message: 'changed since you looked', current_sha256: H('0') } } }
      : o.url.endsWith('/items/3/status') ? { statusCode: 409, body: { detail: { message: 'cannot move from draft to approved', current: 'draft' } } }
        : o.url.endsWith('/items/2/status') ? { statusCode: 428, body: { detail: 'this item needs a bound approval' } }
          : { statusCode: 200, body: {} }],
  ['item 4 not found, text answer on 5', o =>
    o.url.includes('/items/4') ? { statusCode: 404, body: { detail: 'item 4 not found' } }
      : o.url.includes('/items/5') ? { statusCode: 502, body: 'Bad Gateway' } : { statusCode: 200, body: {} }],
];
const FAIL_BATCH = ['Sam', [{ id: 1, ...T('approve') }, { id: 2, ...T('approve') }, { id: 3, ...T('edit', { text: 'Edited' }) },
  { id: 4, ...T('reject_drop') }, { id: 5, ...T('back_to_draft') }]];
const n8nFail = FAILS.map(([name, reply]) => {
  const { review, final, d } = n8n(...FAIL_BATCH);
  const stage = k => final.ops.filter(o => o.stage === k).map(o => ({ ...o, body: JSON.stringify(o.body) }));
  const nodes = { 'Final decisions': final, 'Decisions': d, 'Review items': review,
    'Stage 1': stage(1), 'Run stage 1': stage(1).map(reply), 'Stage 2': stage(2), 'Run stage 2': stage(2).map(reply) };
  const summary = one(js('Summary'), {}, nodes, env);
  const result = one(js('Result'), {}, { ...nodes, 'Summary': summary }, env);
  const failures = [1, 2].flatMap(k => nodes[`Run stage ${k}`].map((r, i) => ({ r, op: nodes[`Stage ${k}`][i] })))
    .filter(({ r }) => r.statusCode >= 300)
    .map(({ r, op }) => ({ item_id: Number(op.url.match(/\/items\/(\d+)/)[1]), status: r.statusCode, detail: (r.body || {}).detail ?? r.body ?? null }));
  const learning = run(js('Learning events'), {}, nodes, env).map(i => i.json).filter(i => !i.noop).map(i => JSON.parse(i.body));
  const engine = run(js('Engine outcomes'), {}, nodes, env).map(i => i.json).filter(i => !i.noop)
    .map(i => ({ path: i.url.slice(env.ENGINE_URL.length), body: JSON.parse(i.body) }));
  return { name, result, failures, learning, engine };
});

const py = spawnSync(process.env.PYTHON || 'python3', ['-c', PY, SERVICE], {
  input: JSON.stringify({ now, items: ITEMS, cases: CASES.map(([, reviewer, decisions]) => ({ reviewer, decisions })), bad: BAD,
    results: n8nFail.map(f => ({ reviewer: FAIL_BATCH[0], decisions: FAIL_BATCH[1], failures: f.failures })) }),
  encoding: 'utf8', maxBuffer: 64 << 20 });
assert.equal(py.status, 0, `python planner failed: ${py.stderr}`);
const out = JSON.parse(py.stdout);

// ---------- 1. the same calendar operations, summaries and learning events per case
const DIFFERENCES = {
  // n8n: stage 2 sets draft with "sent back for an automatic rewrite" and starts workflow 49.
  // The service: the same reject, then draft with the reason for a person to rewrite.
  rewrite: (n, s, [, , [dec]]) => {
    assert.deepEqual(s.ops.slice(0, 1), n.ops.slice(0, 1), 'the reject is the same');
    assert.deepEqual(n.ops[1].body, { status: 'draft', note: 'sent back for an automatic rewrite' });
    assert.deepEqual(s.ops[1], { ...n.ops[1], body: { status: 'draft', note: `sent back to rewrite by hand: ${dec.reason}` } });
    assert.equal(n.revisions.length, 1, 'n8n starts a rewrite');
    assert.match(s.summary[0], /rejected, back to draft to rewrite by hand \("too salesy"\); automatic rewrites run only through n8n/);
    assert.deepEqual(s.events, n.events);
  },
  // Edited video: both save the edit, remove the old video and go to draft (n8n because VIDEO_URL
  // is unset here; with it set n8n re-renders and approves). Only the note and summary differ.
  video: (n, s) => {
    assert.equal(n.videos, 1);
    assert.deepEqual(s.ops.map(o => [o.stage, o.method, o.path, o.body.status]), n.ops.map(o => [o.stage, o.method, o.path, o.body.status]));
    assert.deepEqual(s.ops[0].body, n.ops[0].body, 'the same PATCH: edited body, video removed, if_match');
    assert.match(n.ops[1].body.note, /^script edited: video could not be re-rendered \(.+\), render it again before approving$/);
    assert.equal(s.ops[1].body.note, 'edited video needs a new render (not approved)');
    assert.match(s.summary[0], /NOT approved, back to draft: your edit is saved, but the video needs a new render/);
    assert.deepEqual([s.events, n.events], [[], []], 'nothing approved, no learning event');
  },
  // A video-channel item with no video yet: n8n without VIDEO_URL approves the edited script
  // (with VIDEO_URL it renders first); the service never approves a script it did not render.
  'video-channel': (n, s) => {
    assert.equal(n.videos, 0);
    assert.deepEqual(n.ops.map(o => o.body.status || 'patch'), ['patch', 'approved']);
    assert.deepEqual(s.ops.map(o => [o.method, o.body.status || 'patch']), [['PATCH', 'patch'], ['POST', 'draft']]);
    assert.deepEqual(s.ops[0].body, { body: 'Hook: Now with words', video_url: null, if_match_sha256: H('1') });
  },
};
let compared = 0;
CASES.forEach((c, i) => {
  const [name, reviewer, decisions, diff] = c;
  const n = n8n(reviewer, decisions);
  const s = out.cases[i];
  if (n.nothing) {
    assert.deepEqual([s.count, s.ops, s.not_in_review], [0, [], n.review.missing], name);
    compared++;
    return;
  }
  const nn = { ops: opsOf(n.final.ops), summary: n.final.summary, events: n.final.events, revisions: n.d.revisions, videos: n.d.videos.length };
  assert.deepEqual(s.not_in_review, n.review.missing, `${name}: not_in_review`);
  if (diff) {
    DIFFERENCES[diff](nn, s, c);
  } else {
    assert.deepEqual(s.ops, nn.ops, `${name}: calendar operations`);
    assert.deepEqual(s.summary, nn.summary, `${name}: summary`);
    assert.deepEqual(s.events, nn.events, `${name}: learning events`);
    assert.deepEqual(nn.revisions, [], `${name}: no automatic rewrite in n8n either`);
  }
  compared++;
});

// ---------- 2. the same refusals, word for word
BAD.forEach((b, i) => {
  const r = one(js('Check request'), { headers: { 'x-control-key': KEY }, body: b }, {}, env);
  assert.equal(out.bad[i], r.ok ? null : r.error, `request ${i}: ${JSON.stringify(b).slice(0, 80)}`);
});

// ---------- 3. the same answer when 19 refuses (stale, 428, other errors), same learning and engine calls
n8nFail.forEach((f, i) => assert.deepEqual(out.results[i], f.result, `${f.name}: answer`));
assert.deepEqual(n8nFail[1].result.stale, [1, 2]);
assert.deepEqual(n8nFail[1].result.failed.map(f => [f.item_id, f.status]), [[3, 409]]);
// Learning events and engine outcomes of the all-accepted batch, against the planner's plan.
const batch = spawnSync(process.env.PYTHON || 'python3', ['-c', String.raw`
import json, sys
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
from app import planner
req = json.load(sys.stdin)
reviewer, ds = planner.validate({"reviewer": req["reviewer"], "decisions": req["decisions"]})
p = planner.plan(ds, req["items"], reviewer, datetime.now(timezone.utc))
print(json.dumps({"events": p["events"], "engine": [{"path": f"/pillars/{p['pillars'][e['item_id']]}/outcomes",
    "body": {"item_id": e["item_id"], "decision": e["decision"]}} for e in p["events"] if e["item_id"] in p["pillars"]]}))
`, SERVICE], { input: JSON.stringify({ items: ITEMS, reviewer: FAIL_BATCH[0], decisions: FAIL_BATCH[1] }), encoding: 'utf8' });
assert.equal(batch.status, 0, batch.stderr);
const pb = JSON.parse(batch.stdout);
assert.deepEqual(pb.events, n8nFail[0].learning, 'learning events');
assert.deepEqual(pb.engine, n8nFail[0].engine, 'engine outcomes');
assert.ok(pb.engine.length === 1 && pb.engine[0].path === '/pillars/2/outcomes');

console.log(`approval parity: ${compared} cases (${Object.keys(DIFFERENCES).length} documented differences), ` +
  `${BAD.length} request checks, ${FAILS.length} failure answers (ok)`);
