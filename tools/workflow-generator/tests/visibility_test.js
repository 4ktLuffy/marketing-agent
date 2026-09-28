// Checks 83 "Ready?" / "Build" and 41 "Visibility section" as they ship (read from workflow.json).
// Run: node tests/visibility_test.js   (after build.py)
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

const W83 = '83-wf-sched-ai-visibility', W41 = '41-wf-sched-weekly-report';
const ready = nodeCode(W83, 'Ready?'), build = nodeCode(W83, 'Build'), weekly = nodeCode(W41, 'Visibility section');
const ENV = { VISIBILITY_URL: 'http://ai-visibility:8000', VISIBILITY_IDEAS_PER_WEEK: '2' };

// Ready?: says what is missing, never runs without a key or an approved set
assert.equal(run(ready, [{ statusCode: 200, body: {} }], {}, {})[0].json.notify, false);
assert.match(run(ready, [{ statusCode: 200, body: { providers_enabled: [], active_set: null } }], {}, ENV)[0].json.text, /no provider/);
assert.match(run(ready, [{ statusCode: 200, body: { providers_enabled: ['groq_knowledge'], active_set: null } }], {}, ENV)[0].json.text, /approved question set/);
assert.equal(run(ready, [{ statusCode: 200, body: { providers_enabled: ['groq_knowledge'], active_set: { id: 1 } } }], {}, ENV)[0].json.go, true);

const RUN = { statusCode: 200, body: { id: 7, status: 'done', set_version: 2, samples: 3 } };
const SUMMARY = { statusCode: 200, body: { brand: 'Northwind Roasters', providers: {
  groq_knowledge: { web_search: false, mention: { rate: 0.1, ci95: [0.03, 0.26] }, citation: null, avg_position: 3,
    share_of_voice: { 'Northwind Roasters': { share: 0.05 } }, errors: 0, skipped: 0,
    trend: { mention_delta: 0.05, citation_delta: null }, branded: { wrong_claims: 1 } },
  openai_search: { web_search: true, mention: { rate: 0, ci95: [0, 0.11] }, citation: { rate: 0, ci95: [0, 0.11] },
    share_of_voice: {}, errors: 1, skipped: 2, trend: null, branded: { wrong_claims: 0 } } } } };
const WRONG = { statusCode: 200, body: { claims: [{ sentence: 'Northwind Roasters sells the Team Box for $99 per month.',
  providers: ['groq_knowledge'], questions: ['How much is the Team Box?'], reasons: ['numbers not in the facts: 99'] }] } };
const gap = q => ({ question: q, competitors: [{ name: 'Atlas Coffee Club', mentions: 3 }], answers: 3, providers: ['openai_search'],
  cited_pages: [{ url: 'https://wirecutter.example/coffee', owner: 'third party' }], brief_keyword: q.toLowerCase(),
  suggested_faq: { answer_first: 'Answer it first.' } });
const GAPS = { statusCode: 200, body: { total: 3, gaps: [gap('Best coffee subscription for remote teams?'), gap('Coffee gift for a team?'), gap('Third one?')] } };
const old = new Date(Date.now() - 20 * 864e5).toISOString();
const IDEAS = { statusCode: 200, body: [{ id: 3, created_at: old, notes: 'visibility gap key [gap:coffee gift for a team] (run 5)' }] };
const nodes = { Run: RUN, Summary: SUMMARY, 'Wrong claims': WRONG, Gaps: GAPS, 'Visibility ideas': IDEAS };

const out = run(build, [{}], nodes, ENV)[0].json;
assert.match(out.text, /groq_knowledge \(no web search\): Northwind Roasters named in 10% \(3–26%\) of answers \+5 pts/);
assert.match(out.text, /openai_search: .* 0% \(0–11%\) of answers, cited in 0% \(0–11%\) \(1 failed, 2 skipped\)/);
assert.match(out.text, /API answers, not what a person sees/);
// cap 2: the wrong claim first, then the first gap; the gift gap was noted 20 days ago (skipped anyway)
assert.equal(out.ideas.length, 2);
assert.equal(out.ideas[0].channel, 'visibility_gap');
assert.equal(out.ideas[0].status, 'idea');
assert.match(out.ideas[0].title, /^AI says: Northwind Roasters sells the Team Box/);
assert.match(out.ideas[1].body, /SEO brief on "best coffee subscription for remote teams\?"/);
assert.match(out.ideas[1].notes, /^visibility gap key \[gap:best coffee subscription for remote teams\]/);
// dedupe within 28 days and weekly cap
const recentIdea = { statusCode: 200, body: [{ id: 9, created_at: new Date().toISOString(),
  notes: 'visibility claim key [claim:northwind roasters sells the team box for 99 per month] (run 6)' }] };
const out2 = run(build, [{}], { ...nodes, 'Visibility ideas': recentIdea }, ENV)[0].json;
assert.equal(out2.ideas.length, 1);   // one used this week, the claim already noted
assert.match(out2.ideas[0].title, /remote teams/);
// cap 5: the claim and the two gaps not noted recently, taking turns
const out4 = run(build, [{}], nodes, { ...ENV, VISIBILITY_IDEAS_PER_WEEK: '5' })[0].json;
assert.deepEqual(out4.ideas.map(i => i.title.split(':')[0]), ['AI says', 'AI visibility gap', 'AI visibility gap']);
// a failed run: message, no ideas
const out3 = run(build, [{}], { ...nodes, Run: { statusCode: 409, body: { detail: 'a run is already running' } } }, ENV)[0].json;
assert.deepEqual(out3.ideas, []);
assert.match(out3.text, /did not finish \(409: a run is already running\)/);

// 41: one line appended; nothing without VISIBILITY_URL
const prev = { markdown: '### Experiments\nnone' };
const w = run(weekly, [{ statusCode: 200, body: { ...SUMMARY.body, run: { id: 7, finished_at: '2026-09-22T06:40:00Z' } } }],
  { 'Suggestions section': prev }, ENV)[0].json;
assert.match(w.markdown, /### Experiments\nnone\n\n### AI visibility\nNorthwind Roasters named in unaided AI answers \(run 7, 2026-09-22\): groq_knowledge 10% \(3–26%\) \+5 pts; openai_search 0% \(0–11%\), cited 0% \(0–11%\)\. 1 sentence/);
assert.deepEqual(run(weekly, [{ statusCode: 200, body: {} }], { 'Suggestions section': prev }, {})[0].json, prev);
console.log('visibility_test: all passed');
