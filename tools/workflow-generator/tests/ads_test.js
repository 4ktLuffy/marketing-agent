// Checks 84's daily "Ready?" / "Build" and 41's ads parts ("Action data", "Check actions",
// "Ads section") as they ship (read from workflow.json). Run: node tests/ads_test.js (after build.py)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const nodeCode = (folder, name) => {
  const wf = JSON.parse(fs.readFileSync(path.join(ROOT, folder, 'workflow.json'), 'utf8'));
  const n = wf.nodes.find(x => x.name === name);
  assert.ok(n, `${folder}: no node "${name}"`);
  return n.parameters.jsCode;
};
const run = (code, input, nodes, env = {}) => new Function('$input', '$', '$env', code)(
  { first: () => ({ json: input[0] }), all: () => input.map(json => ({ json })) },
  n => {
    assert.ok(n in nodes, `node ${n} not mocked`);
    const v = [].concat(nodes[n]);
    return { first: () => ({ json: v[0] }), all: () => v.map(json => ({ json })) };
  },
  env);

const W84 = '84-ads-sync/n8n', W41 = '41-wf-sched-weekly-report';
const ENV = { ADS_URL: 'http://ads-sync:8000' };

// ---- 84 Ready?
const ready = nodeCode(W84, 'Ready?');
assert.equal(run(ready, [{ statusCode: 200, body: { configured: true } }], {}, {})[0].json.notify, false);  // no ADS_URL
assert.equal(run(ready, [{ statusCode: 200, body: { configured: true } }], {}, ENV)[0].json.go, true);
const nc = run(ready, [{ statusCode: 200, body: { configured: false, config_errors: ['no platform configured'] } }], {}, ENV)[0].json;
assert.equal(nc.go, false); assert.equal(nc.notify, false); assert.match(nc.text, /no platform configured/);
const down = run(ready, [{ statusCode: 502, body: { detail: 'bad gateway' } }], {}, ENV)[0].json;
assert.equal(down.notify, true); assert.match(down.text, /did not answer \(502: bad gateway\)/);

// ---- 84 Build: new alerts notify; repeats do not; a failed platform always notifies
const build = nodeCode(W84, 'Build');
const OVER = { rule: 'overspend', severity: 'medium', campaign: 'autumn-launch',
  message: 'autumn-launch: spent 3310.00 of a 3000.00 budget by day 27 of 30; plan was 2700.00 (23% over).' };
const ZERO = { rule: 'spend_no_conversions', severity: 'high', campaign: 'meta:23850002',
  message: 'meta:23850002: 60.00 spent over the last 3 days with 0 conversions.' };
const SYNC_OK = { statusCode: 200, body: { platforms: { meta: { ok: true, rows: 14 }, google: { ok: true, rows: 7 } }, warnings: [] } };
let out = run(build, [{}], { Sync: SYNC_OK, 'Check alerts': { statusCode: 200, body: { as_of: '2026-09-27', alerts: [ZERO, OVER], new: [ZERO, OVER] } } })[0].json;
assert.equal(out.notify, true); assert.equal(out.new_alerts, 2);
assert.match(out.text, /\*Paid ads alerts\* \(as of 2026-09-27\)/);
assert.match(out.text, /- \[high\] spend no conversions: meta:23850002: 60\.00 spent/);
assert.match(out.text, /- \[medium\] overspend: autumn-launch: spent 3310\.00/);
out = run(build, [{}], { Sync: SYNC_OK, 'Check alerts': { statusCode: 200, body: { alerts: [ZERO, OVER], new: [] } } })[0].json;
assert.equal(out.notify, false); assert.equal(out.text, '');
const SYNC_HALF = { statusCode: 200, body: { platforms: { meta: { ok: true }, google: { ok: false, error: 'Google rejected GOOGLE_ADS_REFRESH_TOKEN (invalid_grant)' } }, warnings: [] } };
out = run(build, [{}], { Sync: SYNC_HALF, 'Check alerts': { statusCode: 200, body: { alerts: [], new: [] } } })[0].json;
assert.equal(out.notify, true); assert.match(out.text, /\*Paid ads sync\*: google: Google rejected GOOGLE_ADS_REFRESH_TOKEN/);
out = run(build, [{}], { Sync: { statusCode: 429, body: { detail: 'Meta rate limit reached. Retry after 420 s.' } },
  'Check alerts': { statusCode: 200, body: { alerts: [], new: [] } } })[0].json;
assert.match(out.text, /sync failed \(429: Meta rate limit reached/);

// ---- 41: facts reach the prompt data; an ads action passes the number check only with those numbers
const SUMMARY = { statusCode: 200, body: { has_data: true,
  window: { current: { start: '2026-09-21', end: '2026-09-27' }, previous: { start: '2026-09-14', end: '2026-09-20' } },
  facts: ['meta ads: spend 770.00 EUR in the last 7 days, 700.00 EUR the 7 days before (+10.0%)',
          'meta ads: 25 conversions in the last 7 days, CPL 30.80 EUR (20.00 EUR the 7 days before, +54.0%)',
          'all ads (EUR): spend 1,050.00 EUR in the last 7 days, 980.00 EUR the 7 days before (+7.1%)',
          `alert overspend: ${OVER.message} (amounts in EUR)`],
  unmapped_spend: { EUR: 140 }, alerts: [OVER] } };
const actionData = nodeCode(W41, 'Action data');
const baseNodes = { 'Last week': { from: '2026-09-21', to: '2026-09-27' }, KPIs: {}, 'Clicks by channel': { body: null },
  'Winning hooks': { body: null }, 'Collect scorecards': { scorecards: [] }, 'One per pillar': [{ noop: true }],
  'Pillar health': [{ body: null }], 'Ads summary': SUMMARY };
const data = run(actionData, [{}], baseNodes, ENV)[0].json;
const parsed = JSON.parse(data.data);
assert.ok(parsed.facts.includes(SUMMARY.body.facts[1]));
assert.equal(JSON.parse(run(actionData, [{}], baseNodes, {})[0].json.data).facts, undefined);  // ADS_URL empty: left out
const noData = { ...baseNodes, 'Ads summary': { statusCode: 200, body: { has_data: false, facts: [] } } };
assert.equal(JSON.parse(run(actionData, [{}], noData, ENV)[0].json.data).facts, undefined);

const check = nodeCode(W41, 'Check actions');
const llm = { output: { headline: 'Meta CPL rose to 30.80 EUR', what_changed: ['Ad spend 1,050.00 EUR (+7.1%)', 'Meta CPL 35 EUR'],
  actions: [{ channel: 'meta ads', action: 'Move 20% of prospecting budget to the best ad set', why: 'CPL 30.80 EUR vs 20.00 EUR the week before (+54.0%).' },
            { channel: 'google ads', action: 'Raise brand search budget', why: 'CPL is 18.5 EUR there.' }] } };
const checked = run(check, [llm], { 'Action data': data }, ENV)[0].json;
assert.deepEqual(checked.actions.map(a => a.channel), ['meta ads']);
assert.match(checked.dropped[0].reason, /number not in the data \(18\.5\)/);
assert.deepEqual(checked.what_changed, ['Ad spend 1,050.00 EUR (+7.1%)']);   // "35 EUR" is not in the data

// ---- 41 Ads section: facts (no alert lines twice), unmapped spend, open alerts; off without ADS_URL
const section = nodeCode(W41, 'Ads section');
const prev = { markdown: '### AI visibility\nline' };
const md = run(section, [prev], { 'Visibility section': prev, 'Ads summary': SUMMARY }, ENV)[0].json.markdown;
assert.match(md, /^### AI visibility\nline\n\n### Ads\nPaid ads 2026-09-21 to 2026-09-27/);
assert.match(md, /- meta ads: 25 conversions in the last 7 days, CPL 30\.80 EUR/);
assert.match(md, /Spend not mapped to a campaign: 140\.00 EUR/);
assert.match(md, /Open alerts:\n\n- \[medium\] autumn-launch: spent 3310\.00/);
assert.ok(!md.includes('- alert overspend'));
assert.equal(run(section, [prev], { 'Visibility section': prev, 'Ads summary': SUMMARY }, {})[0].json.markdown, prev.markdown);
assert.equal(run(section, [prev], { 'Visibility section': prev, 'Ads summary': { statusCode: 502, body: {} } }, ENV)[0].json.markdown, prev.markdown);

console.log('ads_test: ok');
