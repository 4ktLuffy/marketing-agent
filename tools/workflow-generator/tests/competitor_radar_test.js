// Checks the competitor radar code as it ships, read from the built workflow.json files:
// 37 "Changes" / "Digest" / "Check brief" (ads from 78, quote check, pricing brief number check)
// and 81 "Candidates" / "Pick pages" (only the homepage's own links survive).
// Run: node tests/competitor_radar_test.js   (after build.py)
// Negative control: at the end, a Digest whose quote check is disabled must let an invented quote through.
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
// nodes: {name: json or [json, ...]}
const run = (code, input, nodes, env = {}) => new Function('$input', '$', '$env', code)(
  { first: () => ({ json: input[0] }), all: () => input.map(json => ({ json })) },
  n => {
    assert.ok(n in nodes, `node ${n} not mocked`);
    const v = [].concat(nodes[n]);
    return { first: () => ({ json: v[0] }), all: () => v.map(json => ({ json })) };
  },
  env);

const W37 = '37-wf-sched-competitor-watch', W79 = '81-wf-tool-track-competitor';
const changes = nodeCode(W37, 'Changes'), digest = nodeCode(W37, 'Digest'), brief = nodeCode(W37, 'Check brief');
const ENV = { AD_LIBRARY_URL: 'http://ad-library-sync:8000' };
const recent = new Date(Date.now() - 3600e3).toISOString(), old = new Date(Date.now() - 3 * 86400e3).toISOString();
const PRICING_DIFF = '--- before\n+++ after\n@@ -1,2 +1,2 @@\n Starter $10 per month\n-Pro $30 per month\n+Pro $25 per month\n+Introducing Team plans for offices';
const PAGE = { changed: [{ id: 1, url: 'https://rival.example.org/pricing', label: 'Rival Beans · pricing', diff: PRICING_DIFF }] };
const AD1 = { ad_id: '11', competitor: 'Rival Beans', change: 'new', first_seen: recent, texts: ['Fresh beans every month. First box free.'],
  link_titles: ['Try Rival'], library_url: 'https://www.facebook.com/ads/library/?id=11' };
const AD2 = { ad_id: '12', competitor: 'Rival Beans', change: 'new', first_seen: old, texts: [], link_titles: [], library_url: 'https://www.facebook.com/ads/library/?id=12' };
const LINKS = [{ competitor: 'Rival Beans', links: [
  { platform: 'meta', market: 'ALL', api: false, url: 'https://www.facebook.com/ads/library/?country=ALL&view_all_page_id=1' },
  { platform: 'meta', market: 'DE', api: true, url: 'https://www.facebook.com/ads/library/?country=DE' },
  { platform: 'google', market: 'ALL', api: false, url: 'https://adstransparency.google.com/?region=anywhere&domain=rival.example.org' },
  { platform: 'linkedin', market: 'ALL', api: false, url: 'https://www.linkedin.com/ad-library/search?accountOwner=Rival+Beans' }] }];
const ok = body => ({ statusCode: 200, body });

// --- Changes: nothing changed and no fresh ad -> stop.
assert.deepEqual(run(changes, [{}], { 'Check pages': { changed: [] }, 'Sync ads': ok({}), 'New ads': ok({ ads: [AD2] }),
  'Ad library links': ok(LINKS) }, ENV), []);
// Only a fresh ad -> go on.
let c = run(changes, [{}], { 'Check pages': { changed: [] }, 'Sync ads': ok({}), 'New ads': ok({ ads: [AD1, AD2] }),
  'Ad library links': ok(LINKS) }, ENV)[0].json;
assert.equal(c.fresh, 1);
assert.match(c.changes, /No watched page changed/);
assert.match(c.ads_text, /"Fresh beans every month\. First box free\."/);
assert.deepEqual(c.ad_texts, ['Fresh beans every month. First box free.', 'Try Rival']);
// AD_LIBRARY_URL empty: 78 is never read, page changes still work.
c = run(changes, [{}], { 'Check pages': PAGE }, {})[0].json;
assert.deepEqual([c.ads, c.links, c.ad_note], [[], [], '']);
// Page changed + ads; sync failed -> note; pricing and launch detected.
c = run(changes, [{}], { 'Check pages': PAGE, 'Sync ads': { statusCode: 503, body: { detail: 'META_AD_LIBRARY_TOKEN is not set' } },
  'New ads': ok({ ads: [AD1, AD2] }), 'Ad library links': ok(LINKS) }, ENV)[0].json;
assert.match(c.ad_note, /META_AD_LIBRARY_TOKEN is not set/);
assert.equal(c.pricing.length, 1);
assert.equal(c.pricing[0].competitor, 'Rival Beans');
assert.deepEqual(c.launches.map(l => l.line), ['Introducing Team plans for offices']);

// --- Digest: exact quotes stay, invented ones go; sections are built in code.
const LLM = '### Rival Beans · pricing\n- Pro dropped: "Pro $25 per month"\n- Their ad says "Fresh beans every month."\n' +
  '- They also claim "the best coffee in Europe"\n- Short "free box" stays\n\n### Ads\n- Pushing "Try Rival" and “First box free.”';
const d = run(digest, [{ output: LLM }], { Changes: c }, ENV)[0].json;
assert.equal(d.removed_quotes, 1);
assert.match(d.digest, /"Pro \$25 per month"/);
assert.match(d.digest, /"Fresh beans every month\."/);
assert.match(d.digest, /“First box free\.”/);
assert.ok(!d.digest.includes('best coffee in Europe'));
assert.match(d.digest, /\[quote removed: not an exact ad or page text\]/);
assert.match(d.digest, /"free box" stays/);  // under 3 words: not checked
assert.match(d.digest, /### Possible launches[\s\S]*"Introducing Team plans for offices"/);
assert.match(d.digest, /- Rival Beans, new \(first seen [\d-]+\): "Fresh beans every month\. First box free\." https:\/\/www\.facebook\.com\/ads\/library\/\?id=11/);
assert.match(d.digest, /\(no text: image or video ad\)/);
assert.match(d.digest, /### Check by hand[\s\S]*\[Meta \(all countries\)\]\([^)]*country=ALL[^)]*\) · \[Google\]\([^)]*\) · \[Linkedin\]/);
assert.ok(!d.digest.includes('country=DE'), 'API-covered markets are not in the manual list');
assert.match(d.digest, /_1 quote\(s\) removed/);
assert.equal(d.pricing.length, 1);

// --- Check brief: lines with a number not in the diff or facts are dropped; saved as a non-publishable idea.
const PC = { competitor: 'Rival Beans', url: 'https://rival.example.org/pricing', diff: PRICING_DIFF, facts: '- Our Pro plan is $28 per month.' };
const out = run(brief, [{ output: { what_changed: 'Pro fell from $30 to $25.', decision: 'counter',
  reasons: ['They now undercut our $28 Pro plan.', 'Competitors cut prices 40% this year.'],
  response_points: ['Stress freshness instead of price.', 'Offer 15% off.'] } }], { 'Pricing changes': PC })[0].json;
assert.equal(out.channel, 'competitor_brief');
assert.equal(out.status, 'idea');
assert.match(out.body, /\$28 Pro plan/);
assert.ok(!out.body.includes('40%') && !out.body.includes('15% off'));
assert.match(out.body, /2 line\(s\) dropped/);
assert.match(out.title, /\(counter\)$/);
// A failed LLM call saves nothing.
assert.deepEqual(run(brief, [{ error: 'timeout' }], { 'Pricing changes': PC }), []);
// 39 never publishes it.
assert.match(nodeCode('39-wf-sched-publisher', Object.values(JSON.parse(fs.readFileSync(path.join(ROOT, '39-wf-sched-publisher/workflow.json'))).nodes)
  .find(n => (n.parameters.jsCode || '').includes('NOT_POSTS')).name), /'competitor_brief'/);

// --- 81: candidates are the homepage's own links; the pick keeps only those.
const cand = nodeCode(W79, 'Candidates'), pick = nodeCode(W79, 'Pick pages');
const HOME = ok({ url: 'https://www.rival.example.org/', title: 'Rival Beans | Coffee subscriptions', og: {},
  link_list: [{ url: 'https://www.rival.example.org/pricing', text: 'Pricing' }, { url: 'https://rival.example.org/about', text: 'About' },
    { url: 'https://www.rival.example.org/login', text: 'Log in' }, { url: 'https://evil.example.net/pricing', text: 'x' },
    { url: 'https://www.rival.example.org/', text: 'Home' }] });
const k = run(cand, [HOME], { Start: { url: 'https://rival.example.org', name: '', markets: 'de, fr, usa' } }, ENV)[0].json;
assert.equal(k.go, true);
assert.equal(k.name, 'Rival Beans');
assert.deepEqual(k.candidates, ['https://www.rival.example.org/pricing', 'https://rival.example.org/about']);
assert.deepEqual(k.markets, ['DE', 'FR']);
assert.equal(run(cand, [HOME], { Start: { url: 'https://rival.example.org' } }, {})[0].json.go, false);
assert.match(run(cand, [{ statusCode: 422, body: { detail: 'non-public address' } }], { Start: { url: 'https://rival.example.org' } }, ENV)[0].json.result,
  /non-public address/);
assert.match(run(cand, [HOME], { Start: { url: 'rival' } }, ENV)[0].json.result, /not a website address/);
const picked = run(pick, [{ output: { pages: [{ url: 'https://www.rival.example.org/pricing', type: 'pricing' },
  { url: 'https://www.rival.example.org/plans', type: 'pricing' }, { url: 'https://evil.example.net/pricing', type: 'pricing' },
  { url: 'https://rival.example.org/about', type: 'about' }, { url: 'https://rival.example.org/about', type: 'about' }] } }], { Candidates: k });
assert.deepEqual(picked.map(i => i.json.url), ['https://www.rival.example.org/pricing', 'https://rival.example.org/about']);
assert.equal(picked[0].json.rejected, 2);
const none = run(pick, [{ error: 'LLM down' }], { Candidates: k });
assert.equal(none.length, 1);
assert.equal(none[0].json.noop, true);

// --- Negative control: without the quote check the invented quote survives, so the test above can fail.
const broken = digest.replace('if (q.split(\' \').length < 3 || sources.some(s => s.includes(q))) return m;', 'return m;');
assert.notEqual(broken, digest, 'negative control did not patch the check');
assert.ok(run(broken, [{ output: LLM }], { Changes: c }, ENV)[0].json.digest.includes('best coffee in Europe'));

// --- 37 monthly positioning map: 78's map -> KB doc, calendar idea (never published), notification.
const pos = nodeCode(W37, 'Positioning map');
const MAP = { id: 3, month: '2026-10', previous_month: '2026-09', markdown: '# Positioning map October 2026\n| Theme | Rival |',
  white_space: [{ theme: 'guarantee', facts: [{ quote: 'returned within 30 days for a full refund', source: { kind: 'fact', ref: 'fact f6' } }] }],
  crowded: [{ theme: 'price', competitors: ['Rival Beans', 'BeanCo'] }],
  shifts: [{ text: 'Rival Beans started talking about price in October 2026' }],
  brands: [{ name: 'Rival Beans' }, { name: 'BeanCo', error: 'nothing to read yet' }] };
let pm = run(pos, [ok(MAP)], {}, ENV)[0].json;
assert.equal(pm.ok, true);
assert.deepEqual(pm.doc, { doc_id: 'positioning-2026-10', title: 'Positioning map October 2026', text: MAP.markdown, source: 'positioning-map' });
assert.equal(pm.idea.channel, 'positioning');
assert.equal(pm.idea.status, 'idea');
assert.match(pm.idea.title, /^Positioning map October 2026: 1 white-space theme\(s\), 1 shift\(s\)$/);
assert.match(pm.notify, /guarantee: "returned within 30 days for a full refund" \(fact f6\)/);
assert.match(pm.notify, /price: Rival Beans, BeanCo/);
assert.match(pm.notify, /Shifts since 2026-09:\*\n- Rival Beans started talking about price in October 2026/);
assert.match(pm.notify, /Not read: BeanCo/);
// Build failed: report it, save nothing (the IF sends ok=false straight to the notification).
pm = run(pos, [{ statusCode: 502, body: { detail: 'every LLM call failed: gateway 503' } }], {}, ENV)[0].json;
assert.equal(pm.ok, false);
assert.equal(pm.doc, undefined);
assert.match(pm.notify, /monthly build failed: every LLM call failed: gateway 503\. Nothing was saved/);
// AD_LIBRARY_URL empty: nothing at all.
assert.deepEqual(run(pos, [ok(MAP)], {}, {}), []);
// 39 never publishes a positioning idea.
assert.match(nodeCode('39-wf-sched-publisher', Object.values(JSON.parse(fs.readFileSync(path.join(ROOT, '39-wf-sched-publisher/workflow.json'))).nodes)
  .find(n => (n.parameters.jsCode || '').includes('NOT_POSTS')).name), /'positioning'/);
// Wiring: monthly trigger at 07:00 on the 1st; the IF's false branch skips the KB and calendar.
const wf37 = JSON.parse(fs.readFileSync(path.join(ROOT, W37, 'workflow.json'), 'utf8'));
assert.equal(wf37.nodes.find(n => n.name === 'Monthly positioning').parameters.rule.interval[0].expression, '0 7 1 * *');
const outs = wf37.connections['Built?'].main;
assert.deepEqual([outs[0].map(x => x.node), outs[1].map(x => x.node)], [['Save map to knowledge base'], ['Positioning webhook set?']]);

console.log('competitor radar: all checks passed');
