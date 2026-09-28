"""Phase 10 workflows (83): AI visibility (GEO) every week, from 82-ai-visibility.

82 asks the approved buyer questions to every AI provider that has a key (official APIs only),
measures mentions, citations, list positions and share of voice in code, and checks sentences
about us against the approved facts (44). This workflow runs it weekly, sends the numbers, and
turns the worst gaps and wrong claims into calendar ideas (channel `visibility_gap`, never
published by 39). Contract: 82-ai-visibility/README.md.
"""
from n8nlib import NOTIFY_ON, Workflow, code, http, if_true, notify, schedule

VIS = "$env.VISIBILITY_URL.replace(/\\/+$/, '')"

HELPERS = r"""
const ok = r => r && r.statusCode >= 200 && r.statusCode < 300;
const bodyOf = r => (r && r.body && typeof r.body === 'object' ? r.body : {});
const errText = r => { r = r || {}; const b = r.body; const e = (b && typeof b === 'object' ? (b.detail ?? b.message) : b) ?? r.error?.message ?? r.error; return e == null || e === '' ? 'no answer' : String(typeof e === 'object' ? JSON.stringify(e) : e).slice(0, 200); };
const pct = r => r && r.rate != null ? `${Math.round(r.rate * 100)}%` + (r.ci95 ? ` (${Math.round(r.ci95[0] * 100)}–${Math.round(r.ci95[1] * 100)}%)` : '') : 'n/a';
const pp = d => d == null ? '' : ` ${d >= 0 ? '+' : ''}${Math.round(d * 100)} pts`;
"""

VIS_READY = HELPERS + r"""
const r = $input.first().json;
const b = bodyOf(r);
if (!$env.VISIBILITY_URL) return [{ json: { go: false, notify: false, text: '*AI visibility*: VISIBILITY_URL is not set. Nothing to do.' } }];
if (!ok(r)) return [{ json: { go: false, notify: true, text: `*AI visibility*: ai-visibility (82) did not answer (${r.statusCode || 'no response'}: ${errText(r)}).` } }];
if (!(b.providers_enabled || []).length) return [{ json: { go: false, notify: true,
  text: '*AI visibility*: no provider has an API key (VIS_OPENAI_API_KEY, VIS_PERPLEXITY_API_KEY, VIS_GROQ_API_KEY, ...). See 82-ai-visibility/README.md.' } }];
if (!b.active_set) return [{ json: { go: false, notify: true,
  text: '*AI visibility*: no approved question set yet. Generate one (82 `POST /questions/generate`), edit it and approve it (`POST /question-sets/<id>/approve`).' } }];
return [{ json: { go: true, providers: b.providers_enabled, set: b.active_set } }];
"""

# Pure: tests/visibility_test.js runs it. Builds the message and the calendar ideas: the top gaps
# and the wrong claims, each at most once per 28 days (calendar notes carry a key), capped per week.
VIS_BUILD = HELPERS + r"""
const run = $('Run').first().json;
const rb = bodyOf(run);
if (!ok(run) || rb.status !== 'done') return [{ json: { notify: true, ideas: [],
  text: `*AI visibility*: the weekly run did not finish (${run.statusCode || 'no response'}: ${rb.status ? rb.status + ', ' + (rb.error || '') : errText(run)}).` } }];
const sm = bodyOf($('Summary').first().json), wr = bodyOf($('Wrong claims').first().json), gp = bodyOf($('Gaps').first().json);
const cal = $('Visibility ideas').first().json;
const items = ok(cal) ? [].concat(cal.body ?? []).flat().filter(i => i && typeof i === 'object') : [];
const now = Date.now(), recent = new Set();
let thisWeek = 0;
for (const i of items) {
  const age = (now - Date.parse(i.created_at || '')) / 864e5;
  const m = String(i.notes || '').match(/visibility (?:gap|claim) key \[([^\]]+)\]/);
  if (!m) continue;
  if (age <= 7) thisWeek++;
  if (age <= 28) recent.add(m[1]);
}
const CAP = Math.max(0, Math.floor(Number($env.VISIBILITY_IDEAS_PER_WEEK ?? 5)));
const keyOf = s => String(s).toLowerCase().replace(/[^a-z0-9 ]+/g, '').replace(/\s+/g, ' ').trim().slice(0, 120);
const brand = sm.brand || 'us';
const lines = [`*AI visibility* (run ${rb.id}, question set v${rb.set_version}, ${rb.samples} answers per question per provider). API answers, not what a person sees in the app.`];
for (const [p, s] of Object.entries(sm.providers || {})) {
  const t = s.trend;
  lines.push(`- ${p}${s.web_search ? '' : ' (no web search)'}: ${brand} named in ${pct(s.mention)} of answers${t ? pp(t.mention_delta) : ''}` +
    (s.citation ? `, cited in ${pct(s.citation)}${t ? pp(t.citation_delta) : ''}` : '') +
    (s.avg_position ? `, average list position ${s.avg_position}` : '') +
    (s.share_of_voice && s.share_of_voice[brand] && s.share_of_voice[brand].share != null ? `, share of voice ${Math.round(s.share_of_voice[brand].share * 100)}%` : '') +
    (s.errors || s.skipped ? ` (${s.errors} failed, ${s.skipped} skipped)` : ''));
}
const gaps = gp.gaps || [], claims = wr.claims || [];
if (gaps.length) {
  lines.push('', `Top gaps (competitors named, ${brand} not; ${gp.total} in total):`);
  for (const g of gaps.slice(0, 3)) lines.push(`- "${g.question}": ${g.competitors.slice(0, 3).map(c => c.name).join(', ')}`);
}
if (claims.length) {
  lines.push('', `AI says things about ${brand} that your approved facts do not support (${claims.length}):`);
  for (const c of claims.slice(0, 3)) lines.push(`- "${c.sentence.slice(0, 160)}" (${c.providers.join(', ')}): ${(c.reasons || [])[0] || 'unsupported'}`);
}
const ideas = [];
let left = Math.max(0, CAP - thisWeek);
// Wrong numbers (prices, sizes) first; then claims and gaps take turns, so neither crowds out the other.
const claimIdea = c => ({ key: 'claim:' + keyOf(c.sentence), item: { title: `AI says: ${c.sentence}`.slice(0, 200), channel: 'visibility_gap', status: 'idea',
  body: [`An AI answer says: "${c.sentence}"`, `Providers: ${c.providers.join(', ')}. Asked: ${c.questions.map(q => `"${q}"`).join('; ')}`,
    `Why it was flagged: ${(c.reasons || []).join('; ') || 'not supported by the approved facts'}`, '',
    'What to do: if it is wrong, correct it where the AI learns it (your own pages first, then the pages it cites); if it is true, add the fact to 05 brand.yaml.'].join('\n') } });
const gapIdea = g => {
  const pages = (g.cited_pages || []).slice(0, 5).map(p => `- ${p.url} (${p.owner})`);
  return { key: 'gap:' + keyOf(g.question), item: { title: `AI visibility gap: ${g.question}`.slice(0, 200), channel: 'visibility_gap', status: 'idea',
    body: [`Buyers ask: "${g.question}". AI answers name ${g.competitors.map(c => `${c.name} (${c.mentions}×)`).join(', ')} and not ${brand} (${g.answers} answers from ${g.providers.join(', ')}).`,
      ...(pages.length ? ['', 'Pages the answers cite:', ...pages] : []), '',
      `Suggested answer-first FAQ: ${g.suggested_faq.answer_first}`, '',
      `Next: ask the chat agent for an SEO brief on "${g.brief_keyword}" (29), or add the FAQ to the page that should rank (68).`].join('\n') } };
};
const cq = [...claims].sort((a, b) => (b.kind === 'number') - (a.kind === 'number')).map(claimIdea).filter(x => !recent.has(x.key));
const gq = gaps.map(gapIdea).filter(x => !recent.has(x.key));
while (left > 0 && (cq.length || gq.length)) {
  for (const q of [cq, gq]) {
    if (!left || !q.length) continue;
    const x = q.shift();
    if (recent.has(x.key)) continue;
    ideas.push({ ...x.item, notes: `visibility ${x.key.split(':')[0]} key [${x.key}] (run ${rb.id})` });
    recent.add(x.key); left--;
  }
}
if (!ideas.length && (claims.length || gaps.length)) lines.push('', `No new calendar ideas (${thisWeek} in the last 7 days, VISIBILITY_IDEAS_PER_WEEK=${CAP}, or already noted in the last 28 days).`);
return [{ json: { notify: true, ideas, text: lines.join('\n') } }];
"""

VIS_EACH = r"""
const b = $input.first().json;
return b.ideas.length ? b.ideas.map(item => ({ json: { save: true, item } })) : [{ json: { save: false } }];
"""

VIS_REPORT = r"""
const b = $('Build').first().json;
let saved = [];
try { saved = $('Save idea').all().map(i => i.json).filter(j => j && j.id); } catch (e) {}
const extra = saved.length ? `\n\nSaved ${saved.length} calendar idea(s) (channel visibility_gap): ${saved.map(s => '#' + s.id).join(', ')}.` : '';
return [{ json: { notify: b.notify, text: b.text + extra } }];
"""

# 41 weekly report: one line from 82's latest summary. Appended after the suggestions section.
WEEKLY_VISIBILITY = HELPERS + r"""
const prev = $('Suggestions section').first().json;
const r = $input.first().json || {};
const b = $env.VISIBILITY_URL && ok(r) ? bodyOf(r) : null;
if (!b || !b.run || !b.providers) return [{ json: prev }];
const brand = b.brand || 'us';
const parts = Object.entries(b.providers).map(([p, s]) => `${p} ${pct(s.mention)}${s.trend ? pp(s.trend.mention_delta) : ''}` +
  (s.citation ? `, cited ${pct(s.citation)}` : ''));
const wrong = Object.values(b.providers).reduce((n, s) => n + ((s.branded || {}).wrong_claims || 0), 0);
const line = ['### AI visibility', `${brand} named in unaided AI answers (run ${b.run.id}, ${b.run.finished_at.slice(0, 10)}): ${parts.join('; ')}.` +
  (wrong ? ` ${wrong} sentence(s) about ${brand} not supported by the approved facts: \`GET /claims/wrong\` on ai-visibility (82).` : '') +
  ' API answers, not what a person sees in the app.'].join('\n');
return [{ json: { ...prev, markdown: [prev.markdown, line].filter(Boolean).join('\n\n') } }];
"""


def wf83():
    wf = Workflow(83, "Schedule · AI visibility")
    t = schedule(wf, "Tuesdays 06:00", "0 6 * * 2")
    h = http(wf, "Visibility health", "GET",
             "={{ $env.VISIBILITY_URL ? %s + '/health' : $env.CALENDAR_URL + '/health' }}" % VIS,
             never_error=True, continue_on_fail=True, full_response=True, timeout=15000)
    rd = code(wf, "Ready?", VIS_READY)
    go = if_true(wf, "Go?", "={{ $json.go }}")
    # wait=true: 82 answers when the run is done (questions × providers × samples; one provider
    # per thread). An hour covers 30 questions × 3 samples at a few seconds per answer.
    rn = http(wf, "Run", "POST", "={{ %s }}/runs" % VIS, "={{ JSON.stringify({}) }}", key=True,
              query={"wait": "true"}, never_error=True, continue_on_fail=True, full_response=True, timeout=3600000)
    opt = dict(never_error=True, continue_on_fail=True, full_response=True, timeout=60000)
    sm = http(wf, "Summary", "GET", "={{ %s }}/summary" % VIS, query={"run_id": "={{ $json.body.id || 0 }}"}, **opt)
    wc = http(wf, "Wrong claims", "GET", "={{ %s }}/claims/wrong" % VIS,
              query={"run_id": "={{ $('Run').first().json.body.id || 0 }}"}, **opt)
    gp = http(wf, "Gaps", "GET", "={{ %s }}/gaps" % VIS,
              query={"run_id": "={{ $('Run').first().json.body.id || 0 }}", "limit": "10"}, **opt)
    ci = http(wf, "Visibility ideas", "GET", "={{ $env.CALENDAR_URL }}/items", query={"channel": "visibility_gap"},
              key=True, **opt)
    bd = code(wf, "Build", VIS_BUILD)
    ea = code(wf, "Ideas", VIS_EACH)
    any_ = if_true(wf, "Any idea?", "={{ $json.save }}")
    sv = http(wf, "Save idea", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json.item) }}", key=True,
              continue_on_fail=True)
    rp = code(wf, "Report", VIS_REPORT, pos=[wf._x, 100])
    gate = code(wf, "Report?", "// Not configured: no notification.\n"
                               "return " + NOTIFY_ON + " ? $input.all().filter(i => i.json.notify) : [];")
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, h, rd, go)
    wf.link(go, rn, src_index=0)
    wf.link(go, gate, src_index=1)
    wf.chain(rn, sm, wc, gp, ci, bd, ea, any_)
    wf.link(any_, sv, src_index=0)
    wf.link(any_, rp, src_index=1)
    wf.link(sv, rp)
    wf.chain(rp, gate, n)
    return wf, {
        "summary": "Every Tuesday it measures whether AI assistants name and cite the brand when buyers ask about the category, through `82-ai-visibility` (official APIs only; nothing is scraped). It checks 82's `/health` (a provider key and an approved question set are needed; otherwise it says what is missing and stops), then starts a run and waits for it (`POST /runs?wait=true`: every approved question × every provider with a key × `VIS_SAMPLES` answers). It reads `GET /summary` (per provider: mention rate on unaided questions with a 95% interval, citation rate, average list position, share of voice, change vs the previous run on the same question set), `GET /claims/wrong` (sentences about the brand that the claim checker (44) could not support with the approved facts) and `GET /gaps` (questions where competitors are named or cited and the brand is not, with the pages the answers cite and a suggested answer-first FAQ).\n\nWrong claims (wrong numbers such as prices first) and gaps, taking turns, become calendar (19) items with channel `visibility_gap` and status `idea`: at most `VISIBILITY_IDEAS_PER_WEEK` per 7 days, and never the same claim or question twice within 28 days (the notes carry `visibility gap key [...]`). The publisher (39) never sends `visibility_gap` items anywhere, even approved. A gap item says which SEO brief (29) to ask for and which page to refresh (68). The summary goes to the notification channel. Every number is labelled as an API answer: it approximates, but is not, what a person sees in the ChatGPT or Perplexity app.",
        "schedule": "Tuesdays 06:00",
        "env": {"VISIBILITY_URL": "82-ai-visibility, e.g. `http://ai-visibility:8000`. Empty = the workflow does nothing",
                "VISIBILITY_IDEAS_PER_WEEK": "most calendar ideas per 7 days; default 5 (0 = none)",
                "NOTIFY_WEBHOOK_URL": "optional; receives the summary"},
        "depends": ["82-ai-visibility", "19-content-calendar"],
    }


ALL_P10 = [wf83]
