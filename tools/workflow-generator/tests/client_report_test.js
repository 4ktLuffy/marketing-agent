// Checks 85 (monthly client report) as it ships, read from the built workflow.json:
// "Month", "Sources", "Report data" (tables from fixture data, skipped and failed sources),
// "Check narrative" (the number and cause check on the LLM summary) and "Calendar item".
// Run: node tests/client_report_test.js   (after build.py)
// Negative controls: a planted wrong number, another client's metric, an unhedged cause, "doubled",
// a spelled-out count: all must be dropped. A hedged cause and a correct number must be kept.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const W85 = '85-wf-sched-client-report';
const WF = JSON.parse(fs.readFileSync(path.join(ROOT, W85, 'workflow.json'), 'utf8'));
const nodeCode = name => {
  const n = WF.nodes.find(x => x.name === name);
  assert.ok(n, `${W85}: no node "${name}"`);
  return n.parameters.jsCode;
};
// nodes: {name: json or [json, ...]}
const run = (code, input, nodes, env = {}) => new Function('$input', '$', '$env', code)(
  { first: () => ({ json: input[0] }), all: () => input.map(json => ({ json })) },
  n => {
    assert.ok(n in nodes, `node ${n} not mocked`);
    const v = [].concat(nodes[n]);
    return { first: () => ({ json: v[0] }), all: () => v.map(json => ({ json })) };
  },
  env);

// ---- Month: last calendar month; an override; January wraps to December of the year before.
const month = nodeCode('Month');
const M = run(month, [{}], {}, { CLIENT_REPORT_MONTH: '2026-08' })[0].json;
assert.deepEqual([M.month, M.label, M.from, M.to, M.prev_from, M.prev_to, M.prev_label, M.days],
  ['2026-08', 'August 2026', '2026-08-01', '2026-08-31', '2026-07-01', '2026-07-31', 'July 2026', 31]);
const J = run(month, [{}], {}, { CLIENT_REPORT_MONTH: '2026-01' })[0].json;
assert.deepEqual([J.prev_from, J.prev_to, J.prev_label], ['2025-12-01', '2025-12-31', 'December 2025']);
const now = run(month, [{}], {}, { CLIENT_REPORT_MONTH: 'garbage' })[0].json;   // bad override: last month
const d = new Date(); const lm = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - 1, 1));
assert.equal(now.from, lm.toISOString().slice(0, 10));

// ---- Sources: only installed services; the rest named as skipped.
const sources = nodeCode('Sources');
const CORE = { BRAND_URL: 'http://brand-service:8000', ANALYTICS_URL: 'http://analytics-ingest:8000/',
  CAMPAIGNS_URL: 'http://campaign-service:8000', CALENDAR_URL: 'http://content-calendar:8000',
  REPORT_URL: 'http://report-builder:8000', GSC_URL: '', ADS_URL: '', UMAMI_SYNC_URL: '' };
const FULL = { ...CORE, GSC_URL: 'http://gsc-sync:8000', ADS_URL: 'http://ads-sync:8000', UMAMI_SYNC_URL: 'http://umami-sync:8000' };
const coreSrc = run(sources, [{}], { Month: M }, CORE).map(i => i.json);
assert.deepEqual(coreSrc.map(s => s.key), ['brand', 'kpis', 'kpis_prev', 'posts', 'campaigns', 'experiments', 'items', 'existing']);
assert.equal(coreSrc[1].url, 'http://analytics-ingest:8000/kpis?from=2026-08-01&to=2026-08-31&compare=false');
assert.equal(coreSrc[0].skipped.length, 5);
assert.ok(coreSrc[0].skipped.some(s => /^paid ads \(84\): ADS_URL is empty$/.test(s)));
assert.ok(coreSrc[0].skipped.some(s => /^Search Console \(67\): GSC_URL is empty$/.test(s)));
const fullSrc = run(sources, [{}], { Month: M }, FULL).map(i => i.json);
assert.equal(fullSrc.length, 13); assert.equal(fullSrc[0].skipped.length, 0);
assert.ok(fullSrc.find(s => s.key === 'gsc').url.endsWith('/kpis?from=2026-08-01&to=2026-08-31&compare=false&source=gsc'));
assert.equal(fullSrc.find(s => s.key === 'ads').url, 'http://ads-sync:8000/summary?days=31&end=2026-08-31&top=5');
const none = run(sources, [{}], { Month: M }, {}).map(i => i.json);   // nothing configured: one harmless item
assert.equal(none.length, 1); assert.equal(none[0].key, 'none');

// ---- Fixtures: client A (Northwind) and client B (Globex, a different install).
const ok = body => ({ statusCode: 200, body });
const kpis = (sessions, clicks, conv, cvr, spend) => ({ totals: { impressions: 0, clicks, sessions, conversions: conv, spend,
  ctr: null, cvr, cpa: conv ? Math.round(spend / conv * 100) / 100 : null }, by_channel: [{ channel: 'linkedin', sessions, clicks }] });
const A = {
  brand: ok({ name: 'Northwind Roasters' }),
  kpis: ok(kpis(1240, 530, 31, 0.025, 0)), kpis_prev: ok(kpis(1100, 610, 28, 0.0255, 0)),
  umami: ok(kpis(980, 0, 22, 0.0224, 0)), umami_prev: ok(kpis(1010, 0, 20, 0.0198, 0)),
  gsc: ok({ totals: { clicks: 412, impressions: 18300 } }), gsc_prev: ok({ totals: { clicks: 377, impressions: 17150 } }),
  posts: ok({ posts: [
    { item_id: 1, title: 'Why we roast on Tuesdays', channel: 'linkedin', clicks: 44, posted_at: '2026-08-05T09:00:00Z' },
    { item_id: 2, title: 'Desk Blend is back', channel: 'x', clicks: 17, posted_at: '2026-08-19T09:00:00Z' },
    { item_id: 3, title: 'July origin notes', channel: 'linkedin', clicks: 63, posted_at: '2026-07-10T09:00:00Z' }] }),
  campaigns: ok([{ id: 1, name: 'Autumn launch', status: 'active', start_date: '2026-08-15', end_date: '2026-09-30',
    kpis: [{ metric: 'sessions', target_value: 2000, actual_value: 1450 }] },
    { id: 2, name: 'Spring sale', status: 'completed', start_date: '2026-03-01', end_date: '2026-03-31', kpis: [] }]),
  experiments: ok([{ id: 3, status: 'decided', variable: 'hook_style', channels: ['linkedin'], decision: 'winner',
    winner: { value: 'question' }, loser: { value: 'fact_led' }, decided_at: '2026-08-22T10:00:00Z' },
    { id: 4, status: 'running', variable: 'post_time', channels: ['x'] }]),
  ads: ok({ has_data: true, window: { current: { start: '2026-08-01', end: '2026-08-31' }, previous: { start: '2026-07-01', end: '2026-07-31' } },
    total: [{ currency: 'EUR', current: { spend: 1050, conversions: 25, cpl: 42, roas: 2.4 }, previous: { spend: 980, conversions: 30, cpl: 32.67, roas: 2.9 } }] }),
  items: ok([
    { id: 11, title: 'Why we roast on Tuesdays', channel: 'linkedin', status: 'published', published_at: '2026-08-05T09:00:00Z' },
    { id: 12, title: 'Desk Blend is back', channel: 'x', status: 'published', published_at: '2026-08-19T09:00:00Z' },
    { id: 13, title: 'Brewing guide', channel: 'blog', status: 'approved', scheduled_at: '2026-08-28T08:00:00Z' },
    { id: 14, title: 'July origin notes', channel: 'linkedin', status: 'published', published_at: '2026-07-10T09:00:00Z' },
    { id: 15, title: 'Refresh: pricing page', channel: 'blog_refresh', status: 'approved', scheduled_at: '2026-08-12T08:00:00Z' },
    { id: 16, title: 'Client report July 2026', channel: 'client_report', status: 'approved', scheduled_at: '2026-08-01T08:00:00Z' }]),
  existing: ok([{ id: 16, title: 'Client report July 2026', channel: 'client_report' }]),
};
const B = { ...A, brand: ok({ name: 'Globex' }), kpis: ok(kpis(4812, 2250, 96, 0.02, 0)), kpis_prev: ok(kpis(5370, 2400, 101, 0.0188, 0)),
  items: ok([]), campaigns: ok([]), experiments: ok([]), posts: ok({ posts: [] }), existing: ok([]) };
const fetchFor = (srcs, data) => srcs.map(s => data[s.key] || { error: { message: 'connect ECONNREFUSED' } });

const reportData = nodeCode('Report data');
const RD = (srcs, data, env = FULL) => run(reportData, fetchFor(srcs, data), { Month: M, Sources: srcs }, env)[0].json;
const a = RD(fullSrc, A);
const row = label => a.rows.find(r => r.label === label);
assert.equal(a.brand, 'Northwind Roasters');
assert.deepEqual(row('Sessions'), { group: 'Website and channels', label: 'Sessions', value: '1,240', previous: '1,100', change: '+140 (+12.7%)', note: null });
assert.equal(row('Clicks').change, '-80 (-13.1%)');
assert.equal(row('Conversion rate').change, '-0.05 pts');
assert.equal(row('Spend'), undefined);                       // zero in both months: no row
assert.equal(row('Visits').value, '980');
assert.equal(row('Clicks from Google search').change, '+35 (+9.3%)');
assert.equal(row('Posts with a tracked link').value, '2');
assert.equal(row('Clicks on those posts (to date)').value, '61');
assert.equal(row('Clicks on those posts (to date)').previous, '63');
assert.equal(row('Ad spend').change, '+70 EUR (+7.1%)');
assert.equal(row('Cost per lead (CPL)').previous, '32.67 EUR');
assert.equal(row('Return on ad spend (ROAS)').change, '-0.50');
assert.match(a.work[0], /^Published or approved in August 2026: 3 pieces \(blog 1, linkedin 1, x 1\)\.$/);   // July item, refresh plan and old report excluded
assert.ok(a.work.some(w => w === 'Campaign "Autumn launch" (active, 2026-08-15 to 2026-09-30): sessions 1,450 of a 2,000 target so far.'));
assert.ok(!a.work.some(w => /Spring sale/.test(w)));
assert.ok(a.work.some(w => w === 'Experiment on hook_style (linkedin): question beat fact_led.'));
assert.ok(a.work.some(w => w === '1 experiment still running.'));
assert.match(a.change_md, /\| Website and channels: Sessions \| 1,240 \| 1,100 \| \+140 \(\+12\.7%\) \|/);
assert.match(a.change_md, /_counted to today, so the earlier month had more time to collect clicks\._/);
assert.equal(a.kpis.delta_pct.sessions, 12.7);
assert.equal(a.kpis.previous.period.from, '2026-07-01');
assert.equal(a.existing_id, null);                            // July's report does not block August
assert.deepEqual(a.sources_failed, []);
// Failed and skipped sources: left out, named, nothing invented.
const coreA = RD(coreSrc, { ...A, kpis_prev: { statusCode: 502, body: { detail: 'down' } }, experiments: undefined }, CORE);
assert.deepEqual(coreA.sources_failed, ['analytics (20), previous month (502)', 'experiments (45) (no response)']);
assert.equal(coreA.sources_skipped.length, 5);
assert.equal(coreA.rows.find(r => r.label === 'Sessions').previous, 'n/a');
assert.equal(coreA.rows.find(r => r.label === 'Sessions').change, 'n/a');
assert.ok(!coreA.rows.some(r => /Paid ads|Google search|Umami/.test(r.group)));
assert.equal(coreA.kpis.delta_pct, null);
// Zero activity and empty sources: a report that says so, no rows, no crash.
const empty = RD(coreSrc, { brand: ok({}), kpis: ok({ totals: { sessions: 0, clicks: 0 } }), kpis_prev: ok({ totals: {} }),
  posts: ok({ posts: [] }), campaigns: ok([]), experiments: ok([]), items: ok([]), existing: ok([]) }, CORE);
assert.deepEqual(empty.rows, []);
assert.deepEqual(empty.work, ['Nothing was published or approved in August 2026.']);
assert.match(empty.change_md, /No numbers were available for this month\./);
assert.equal(empty.brand, '');
// A report for this month already exists: not made again.
assert.equal(RD(coreSrc, { ...A, existing: ok([{ id: 40, title: 'Client report August 2026' }]) }, CORE).existing_id, 40);
assert.equal(RD(coreSrc, { ...A, existing: ok([{ id: 40, title: 'Client report August 2026', status: 'rejected' }]) }, CORE).existing_id, null);
const b = RD(fullSrc, B);
assert.equal(b.rows.find(r => r.label === 'Sessions').value, '4,812');

// ---- Check narrative
const check = nodeCode('Check narrative');
const CK = (summary, data = a) => run(check, [{ output: { summary } }], { 'Report data': data })[0].json;
let c = CK(['Sessions rose to 1,240 in August 2026, up 12.7% on July.',
  'We published 3 pieces, and the Autumn launch campaign started.',
  'The new posts may have helped, but we can\'t tell yet.']);
assert.equal(c.kept.length, 3); assert.equal(c.dropped.length, 0);
// Negative control 1: a planted wrong number.
c = CK(['Sessions rose to 1,240, up 14% on July.', 'Clicks from Google search reached 412.']);
assert.deepEqual(c.kept, ['Clicks from Google search reached 412.']);
assert.equal(c.dropped_number, 1); assert.match(c.dropped[0].detail, /not in the data: 14/);
// Negative control 2: another client's metric (Globex's 4,812 sessions) in Northwind's report.
c = CK(['Sessions reached 4,812 this month.']);
assert.equal(c.kept.length, 0); assert.equal(c.dropped_number, 1);
assert.equal(CK(['Sessions reached 4,812 this month.'], b).kept.length, 1);     // the same sentence is fine in Globex's own report
// Negative control 3: unhedged causes are dropped; hedged ones kept.
c = CK(['Sessions grew because of the Autumn launch campaign.', 'The LinkedIn posts drove more conversions.',
  'Our question hooks led to 61 clicks.', 'Thanks to the new blog, search clicks rose to 412.',
  'The question hooks may have led to more clicks.', 'Search clicks rose to 412; it is too early to say why.',
  'The campaign likely helped sessions grow, though we cannot tell yet.']);
assert.equal(c.dropped_cause, 4); assert.equal(c.kept.length, 3);
assert.ok(c.kept[0].startsWith('The question hooks may'));
assert.match(c.dropped[0].detail, /claims a cause \("because"\) without hedging/);
// A hedge does not rescue a wrong number; "doubled", "twice" and spelled-out counts are numbers too.
c = CK(['Sessions may have risen 37% thanks to the posts.', 'Clicks doubled.', 'We ran seven campaigns.', 'We posted twice a week.',
  'The campaign started on 2026-08-16.', 'We published on 7 days.']);
assert.equal(c.kept.length, 0); assert.equal(c.dropped_number, 6);   // 7 appears only inside a date (2026-07-01)
assert.equal(CK(['The Autumn launch started on 2026-08-15.']).kept.length, 1);
// Negative control 4: the right number with the wrong direction. Checked only when a sentence names
// one measure whose rows all move the same way, and is not about the future.
c = CK(['Clicks from Google search fell to 412.', 'Sessions dropped to 1,240 this month.',
  'Sessions stayed the same.', 'Ad conversions went up to 25.', 'Cost per lead went down.']);
assert.equal(c.dropped_direction, 5, JSON.stringify(c.dropped)); assert.equal(c.kept.length, 0);
assert.match(c.dropped[0].detail, /says down; the data says \+35/);
c = CK(['Sessions rose to 1,240.', 'Ad conversions fell to 25.', 'Search clicks grew to 412.',
  'Conversions went up.',                        // 3 rows (+3, +2, -5): can't tell which, not checked
  'Visits stayed about the same.',               // -3%: close enough to "the same"
  'Sessions rose while clicks fell.',            // two measures: not checked
  'Cost per lead rose to 42 EUR.']);
assert.equal(c.dropped.length, 0, JSON.stringify(c.dropped)); assert.equal(c.kept.length, 5);   // 7 kept, cut to 5
assert.equal(CK(['Next month we aim for more sessions.'], b).dropped.length, 0);   // a goal, not a claim
assert.equal(CK(['Sessions went up this month.'], b).dropped_direction, 1);        // Globex's sessions fell
assert.equal(CK(['Sales may be lower, likely from reduced ad spend.']).dropped_direction, 1);   // spend rose; a hedge does not fix a direction
// A ratio the model worked out ("over 2x", "3 times") is dropped; one the data states (ROAS 2.40x) is kept.
c = CK(['Sessions surged by over 2x.', 'Conversions grew 3 times over.', 'Return on ad spend was 2.40x.']);
assert.deepEqual(c.kept, ['Return on ad spend was 2.40x.']); assert.equal(c.dropped_number, 2);
// At most 5 sentences reach the client; the cut is counted in the notes.
c = CK(Array.from({ length: 7 }, () => 'Sessions were 1,240.'));
assert.equal(c.kept.length, 5); assert.equal(c.cut, 2); assert.match(c.notes, /summary cut to 5 sentences \(2 more were fine but too many\)/);
// "data-driven" is not a cause; multi-sentence strings are split and checked one by one.
c = CK(['A data-driven month. Sessions were 1,240. Visits fell because of the heat.']);
assert.deepEqual(c.kept, ['A data-driven month.', 'Sessions were 1,240.']); assert.equal(c.dropped_cause, 1);
// Dropped sentences go to the reviewer notes, never to the client text.
c = CK(['Sessions reached 4,812.', 'Sessions were 1,240 in August 2026.']);
assert.ok(!c.highlights_md.includes('4,812')); assert.ok(c.notes.includes('4,812'));
assert.match(c.notes, /summary: 2 sentence\(s\) written, 1 kept, 1 dropped for a number not in the data, 0 for the wrong direction, 0 for an unhedged cause/);
assert.match(c.highlights_md, /^Sessions were 1,240 in August 2026\.\n\n### What we did in August 2026/);
// Every number in the kept text is in the data (by construction).
const nums = s => (String(s).replace(/(\d),(?=\d{3}\b)/g, '$1').match(/\d+(?:\.\d+)?/g) || []).map(Number);
const known = new Set(nums(a.data));
assert.ok(nums(c.kept.join(' ')).every(n => known.has(n)));
// LLM failed: tables only, and the notes say so.
const failed = run(check, [{ error: { message: 'timeout of 300000ms exceeded' } }], { 'Report data': coreA })[0].json;
assert.equal(failed.llm_failed, true); assert.deepEqual(failed.kept, []);
assert.match(failed.highlights_md, /^### What we did in August 2026/);
assert.match(failed.notes, /the LLM failed \(timeout of 300000ms exceeded\); the report has the tables only/);
assert.match(failed.notes, /sources skipped \(not installed\): .*ADS_URL is empty/);
assert.match(failed.notes, /sources failed: analytics \(20\), previous month \(502\)/);

// ---- Calendar item: in_review, channel client_report, the title; skipped when the month exists.
const item = nodeCode('Calendar item');
const ckA = CK(['Sessions were 1,240 in August 2026.']);
let it = run(item, [ok({ html: '<html>', markdown: '# Northwind Roasters: monthly report, August 2026\n...' })],
  { 'Report data': a, 'Check narrative': ckA }, CORE)[0].json;
assert.equal(it.save, true);
assert.deepEqual([it.item.title, it.item.channel, it.item.status], ['Client report August 2026', 'client_report', 'in_review']);
assert.match(it.item.notes, /^client report 2026-08\nsummary: 1 sentence/);
it = run(item, [{ statusCode: 500, body: {} }], { 'Report data': a, 'Check narrative': ckA }, CORE)[0].json;   // 21 down: plain markdown
assert.match(it.item.body, /^# Northwind Roasters: monthly report, August 2026\n\nSessions were 1,240/);
assert.match(it.item.notes, /report-builder \(21\) failed \(500\)/);
it = run(item, [ok({ markdown: 'x' })], { 'Report data': { ...a, existing_id: 40 }, 'Check narrative': ckA }, CORE)[0].json;
assert.equal(it.save, false); assert.match(it.text, /already in the calendar \(#40\); not made again\. Reject it to rebuild it\./);

// ---- The publisher (39) never sends a client report, even approved.
const pub = JSON.parse(fs.readFileSync(path.join(ROOT, '39-wf-sched-publisher', 'workflow.json'), 'utf8'));
const one = pub.nodes.find(n => n.name === 'One per item').parameters.jsCode;
const approved = [{ id: 50, channel: 'client_report', status: 'approved', title: 'Client report August 2026', body: 'x' },
  { id: 51, channel: 'linkedin', status: 'approved', title: 'post', body: 'x' }];
const sent = run(one, [{ body: approved }], {}, { PUBLISH_WEBHOOK_URL: 'http://postiz-bridge:8000/publish', CMS_PUBLISH_URL: 'x' }).map(i => i.json.id);
assert.deepEqual(sent, [51]);

// ---- Wiring: the owner is notified; the save happens only on the Save? true branch.
const conns = WF.connections;
assert.deepEqual(conns['Save?'].main[0].map(c => c.node), ['Save for review']);
assert.deepEqual(conns['Save?'].main[1].map(c => c.node), ['Summary']);
assert.ok(!WF.nodes.some(n => n.type === 'n8n-nodes-base.emailSend'));   // nothing is mailed to anyone

console.log('client_report_test: ok');
