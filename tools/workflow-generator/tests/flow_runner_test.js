// Checks 86's 15-minute "Ready?" / "Build" / "Report?" as they ship (read from workflow.json).
// Run: node tests/flow_runner_test.js (after build.py)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const wf = JSON.parse(fs.readFileSync(path.join(ROOT, '86-flow-runner/n8n/workflow.json'), 'utf8'));
const node = name => { const n = wf.nodes.find(x => x.name === name); assert.ok(n, `no node "${name}"`); return n; };
const run = (code, input, nodes, env = {}) => new Function('$input', '$', '$env', code)(
  { first: () => ({ json: input[0] }), all: () => input.map(json => ({ json })) },
  n => {
    assert.ok(n in nodes, `node ${n} not mocked`);
    const v = [].concat(nodes[n]);
    return { first: () => ({ json: v[0] }), all: () => v.map(json => ({ json })) };
  },
  env);
const ENV = { FLOW_URL: 'http://flow-runner:8000' };

// ---- Wiring: every 15 minutes; the sync carries the approver key, the tick does not need it.
assert.equal(node('Every 15 minutes').parameters.rule.interval[0].expression, '*/15 * * * *');
const headers = n => node(n).parameters.headerParameters.parameters.map(h => h.name);
assert.deepEqual(headers('Sync reviews'), ['X-API-Key', 'X-Approver-Key']);
assert.deepEqual(headers('Tick'), ['X-API-Key']);
assert.match(node('Tick').parameters.url, /\/tick$/);
assert.equal(wf.connections['Sync reviews'].main[0][0].node, 'Tick');

// ---- Ready?: skipped quietly without FLOW_URL; a dead service notifies.
const ready = node('Ready?').parameters.jsCode;
let r = run(ready, [{ statusCode: 200, body: { status: 'ok' } }], {}, {})[0].json;
assert.equal(r.go, false); assert.equal(r.notify, false);
assert.equal(run(ready, [{ statusCode: 200, body: { status: 'ok' } }], {}, ENV)[0].json.go, true);
r = run(ready, [{ statusCode: 502, body: { detail: 'bad gateway' } }], {}, ENV)[0].json;
assert.equal(r.go, false); assert.equal(r.notify, true); assert.match(r.text, /did not answer \(502: bad gateway\)/);

// ---- Build
const build = node('Build').parameters.jsCode;
const SYNC_EMPTY = { statusCode: 200, body: { created: [], approved: [], rejected: [], waiting: ['welcome v2'], waiting_for_approver_key: [], problems: [] } };
const TICK_QUIET = { statusCode: 200, body: { dry_run: true, paused: false, sent: 0, failed: 0, capped: false, by_flow: {}, notices: [] } };
const b = (sync, tick) => run(build, [{}], { 'Sync reviews': sync, Tick: tick })[0].json;

// Negative: nothing happened (also: paused or capped again after the first notice) -> no notification.
let out = b(SYNC_EMPTY, TICK_QUIET);
assert.equal(out.notify, false); assert.equal(out.text, '');
out = b(SYNC_EMPTY, { statusCode: 200, body: { ...TICK_QUIET.body, paused: true } });
assert.equal(out.notify, false);
out = b(SYNC_EMPTY, { statusCode: 200, body: { ...TICK_QUIET.body, capped: true, sent: 0 } });
assert.equal(out.notify, false);

// Sends: dry run says nothing was delivered.
out = b(SYNC_EMPTY, { statusCode: 200, body: { ...TICK_QUIET.body, sent: 3, by_flow: { welcome: 2, winback: 1 } } });
assert.equal(out.notify, true); assert.equal(out.sent, 3);
assert.match(out.text, /\*Email flows \(dry run\)\*: 3 emails written to the outbox, nothing delivered \(welcome 2, winback 1\)\./);
out = b(SYNC_EMPTY, { statusCode: 200, body: { ...TICK_QUIET.body, dry_run: false, sent: 1, by_flow: { welcome: 1 } } });
assert.match(out.text, /\*Email flows\*: 1 email sent \(welcome 1\)\./);

// Cap and pause notices (86 sends each once), failures.
out = b(SYNC_EMPTY, { statusCode: 200, body: { ...TICK_QUIET.body, capped: true, sent: 200, by_flow: { welcome: 200 },
  notices: ['Daily cap of 200 emails reached; the rest wait for tomorrow.'] } });
assert.match(out.text, /Daily cap of 200 emails reached/);
out = b(SYNC_EMPTY, { statusCode: 200, body: { ...TICK_QUIET.body, paused: true, notices: ['Flows are paused (wrong offer). Nothing is sent.'] } });
assert.equal(out.notify, true); assert.match(out.text, /paused \(wrong offer\)/);
out = b(SYNC_EMPTY, { statusCode: 200, body: { ...TICK_QUIET.body, dry_run: false, failed: 2 } });
assert.match(out.text, /\*Email flow problems\*: 2 sends failed/);
out = b(SYNC_EMPTY, { statusCode: 401, body: { detail: 'missing or wrong X-API-Key' } });
assert.match(out.text, /tick failed \(401: missing or wrong X-API-Key\)/); assert.equal(out.sent, 0);

// Review sync: waiting for approval (with the form link), approved, rejected, problems.
out = b({ statusCode: 200, body: { ...SYNC_EMPTY.body, created: [{ version: 'welcome v3', calendar_item_id: 12 }] } }, TICK_QUIET);
assert.equal(out.waits, true); assert.match(out.text, /waiting for approval\*: welcome v3/);
out = b({ statusCode: 200, body: { ...SYNC_EMPTY.body, approved: ['winback v2'], rejected: ['welcome v4'] } }, TICK_QUIET);
assert.equal(out.waits, false);
assert.match(out.text, /approved\*: winback v2/); assert.match(out.text, /rejected\*: welcome v4/);
out = b({ statusCode: 200, body: { ...SYNC_EMPTY.body, waiting_for_approver_key: ['welcome v2'],
  problems: ['welcome v5: the approved text could not be read'] } }, TICK_QUIET);
assert.match(out.text, /no APPROVER_KEY: welcome v2; welcome v5: the approved text could not be read/);
out = b({ statusCode: 500, body: 'boom' }, TICK_QUIET);
assert.match(out.text, /review sync failed \(500: boom\)/);

// ---- Report?: nothing goes out without a destination.
const gate = node('Report?').parameters.jsCode;
const items = [{ notify: true, text: 'x' }, { notify: false, text: '' }];
assert.equal(run(gate, items, {}, {}).length, 0);
assert.equal(run(gate, items, {}, { NOTIFY_WEBHOOK_URL: 'http://hook' }).length, 1);

console.log('flow_runner_test: ok');
