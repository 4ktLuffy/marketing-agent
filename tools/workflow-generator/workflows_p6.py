"""Phase 6 workflows (68, 69): Search Console data (67-gsc-sync) into content work.

68 refreshes pages that are losing Google clicks (night-5 research capability #1: HubSpot
doubled leads by updating old posts). 69 turns striking-distance queries (position 5-20,
many impressions) into SEO briefs through the existing brief tool (29).
"""
from n8nlib import NOTIFY_ON, Workflow, call_workflow, code, gateway, http, if_true, loop_one_by_one, notify, schedule

GSC = "$env.GSC_URL.replace(/\\/+$/, '')"

HELPERS = r"""
// Full-response HTTP nodes: {statusCode, body}. A list body can arrive as [list].
const listOf = r => { const raw = [].concat((r || {}).body ?? []); return (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(x => x && typeof x === 'object'); };
const errText = r => { r = r || {}; const b = r.body; const e = (b && typeof b === 'object' ? (b.detail ?? b.message) : b) ?? r.error?.message ?? r.error; return e == null || e === '' ? '' : String(typeof e === 'object' ? JSON.stringify(e) : e).slice(0, 200); };
const ok = r => r && r.statusCode >= 200 && r.statusCode < 300;
const num = (v, d) => { const n = Number(v); return v === undefined || v === null || v === '' || !Number.isFinite(n) || n < 0 ? d : n; };
"""


def gsc_gate(wf, label):
    """GSC health -> Configured? (code). Not set or not configured: {go: false, text}."""
    h = http(wf, "GSC health", "GET",
             "={{ $env.GSC_URL ? %s + '/health' : $env.CALENDAR_URL + '/health' }}" % GSC,
             never_error=True, continue_on_fail=True, full_response=True, timeout=15000)
    c = code(wf, "Configured?", HELPERS + r"""
const r = $input.first().json;
const b = r.body && typeof r.body === 'object' ? r.body : {};
// Nothing to do without Search Console: finish with a message, no error, no notification.
if (!$env.GSC_URL) return [{ json: { go: false, notify: false, text: '*%s*: GSC_URL is not set, so there is no Search Console data. Nothing to do.' } }];
if (b.configured !== true) return [{ json: { go: false, notify: false,
  text: `*%s*: gsc-sync (67) is not configured (${(b.config_errors || []).join('; ') || errText(r) || 'no answer from ' + $env.GSC_URL}). See 67-gsc-sync/README.md. Nothing to do.` } }];
return [{ json: { go: true, site: b.site } }];
""" % (label, label))
    return h, c


def gsc_sync(wf):
    # A failed sync is not fatal: the read endpoints still answer from the last good sync.
    return http(wf, "Sync", "POST", "={{ %s }}/sync" % GSC, "={{ JSON.stringify({ days: 28 }) }}", key=True,
                never_error=True, full_response=True, timeout=120000)


# --------------------------------------------------------------------------- 68 content refresh

REFRESH_PICK = HELPERS + r"""
const sync = $('Sync').first().json;
const dec = $('Declining pages').first().json;
const items = listOf($input.first().json);
const CAP = Math.floor(num($env.REFRESH_PER_WEEK, 3));
const notes = [];
if (!ok(sync)) notes.push(`sync failed (${sync.statusCode || 'no response'}: ${errText(sync)}); used the last good sync`);
if (!ok(dec) || !Array.isArray((dec.body || {}).pages)) return [{ json: { pick: false, notify: true,
  text: `*Content refresh*: no declining-pages data from gsc-sync (${dec.statusCode || 'no response'}: ${errText(dec)}).`, notes } }];
// A page refreshed in the last 60 days (calendar note "refresh of <url> (") is left alone:
// its new version needs time to be re-crawled and measured.
const now = Date.now(), recent = new Map();
let thisWeek = 0;
for (const i of items) {
  const age = (now - Date.parse(i.created_at || '')) / 864e5;
  const urls = [...String(i.notes || '').matchAll(/refresh of (\S+) \(/g)].map(m => m[1]);
  if (!urls.length) continue;
  if (age <= 7) thisWeek++;
  if (age <= 60) for (const u of urls) if (!recent.has(u)) recent.set(u, i.id);
}
const pages = dec.body.pages;
const skipped = pages.filter(p => recent.has(p.page));
for (const p of skipped) notes.push(`${p.page}: refreshed in the last 60 days (calendar #${recent.get(p.page)})`);
const left = Math.max(0, CAP - thisWeek);
const todo = pages.filter(p => !recent.has(p.page));
const picked = todo.slice(0, left);
if (todo.length > picked.length) notes.push(`${todo.length - picked.length} more declining page(s) wait for next week (REFRESH_PER_WEEK=${CAP}, ${thisWeek} refreshed in the last 7 days)`);
if (picked.length) return picked.map(p => ({ json: { pick: true, page: p.page,
  clicks_before: p.previous.clicks, clicks_now: p.current.clicks, drop_pct: Math.round(p.drop * 100),
  declining_queries: p.top_queries || [], notes } }));
return [{ json: { pick: false, notify: notes.length > 0, notes, text: [
  `*Content refresh*: nothing to refresh this week (${pages.length} declining page(s) with at least ${dec.body.min_clicks} clicks before and a ${Math.round(dec.body.drop * 100)}% drop).`,
  ...notes.map(n => `- ${n}`)].join('\n') } }];
"""

REFRESH_VARS = HELPERS + r"""
const p = $('Loop').first(1).json;          // loop output 1 = the page of this round
const ex = $('Extract page').first().json;
const pq = $input.first().json;
const page = ok(ex) && ex.body && typeof ex.body === 'object' ? ex.body : null;
if (!page || !String(page.text || '').trim())
  return [{ json: { ok: false, page: p.page, error: `could not read the page (${ex.statusCode || 'no response'}: ${errText(ex)})` } }];
// The page's top queries in the previous window (what it used to win), current numbers beside.
const src = ok(pq) && Array.isArray((pq.body || {}).queries) && pq.body.queries.length ? pq.body.queries : p.declining_queries;
const top_queries = (src || []).slice(0, 10).map(q => ({ query: q.query,
  clicks_before: q.previous.clicks, clicks_now: q.current.clicks,
  impressions_before: q.previous.impressions, impressions_now: q.current.impressions,
  position_before: q.previous.position, position_now: q.current.position,
  // Differences worked out here: the model's own arithmetic ("lost 21 clicks") failed the number check.
  clicks_lost: Math.max(0, q.previous.clicks - q.current.clicks),
  places_down: q.previous.position != null && q.current.position != null && q.current.position > q.previous.position
    ? Math.round((q.current.position - q.previous.position) * 10) / 10 : null }));
const title = String(page.title || '').trim() || p.page;
const vars = { page_url: p.page, page_title: title, page_text: String(page.text).slice(0, 12000), top_queries,
  clicks_now: p.clicks_now, clicks_before: p.clicks_before, clicks_lost: p.clicks_before - p.clicks_now, drop_pct: p.drop_pct };
return [{ json: { ok: true, page: p.page, title, vars } }];
"""

REFRESH_CHECK = HELPERS + r"""
// A small model invents numbers and "facts". Checked here, not trusted:
// - every number in a change's "why" and "what" must appear in the input (GSC numbers, page
//   text, approved facts; thousands separators ignored), and a "why" must cite a query or a number;
// - update_fact must name an approved fact label (05 /facts) or it is dropped;
// - an FAQ answer with an unknown number is dropped; a title/meta with one keeps the old text.
const v = $('Refresh vars').first().json;
const g = $input.first().json;
const o = g && g.output && typeof g.output === 'object' ? g.output : null;
if (!o) return [{ json: { save: false, page: v.page, error: `no refresh plan from the model (${errText(g) || 'gateway error'})` } }];
const fr = $('Approved facts').first().json;
const facts = Array.isArray((fr.body || {}).facts) ? fr.body.facts : [];
const factById = new Map(facts.map(f => [String(f.id), String(f.text)]));
const strip = s => String(s || '').replace(/\[?\bf\d+\b\]?/gi, ' ').replace(/\bH[1-6]\b/g, ' ');
const nums = s => (strip(s).replace(/(\d),(?=\d{3}\b)/g, '$1').match(/\d+(?:\.\d+)?/g) || []).map(Number);
const x = v.vars;
const known = new Set([...nums(JSON.stringify(x.top_queries)), ...nums([x.clicks_now, x.clicks_before, x.clicks_lost, x.drop_pct].join(' ')),
  ...nums(x.page_text), ...nums(x.page_title), ...nums(facts.map(f => f.text).join(' ')), new Date().getFullYear()]);
const unknown = s => nums(s).filter(n => !known.has(n));
const queries = x.top_queries.map(q => String(q.query).toLowerCase());
const citesQuery = s => queries.some(q => String(s).toLowerCase().includes(q));
// The model sometimes copies the [f3] labels into page copy; they are not for readers.
const unlabel = s => String(s || '').replace(/\s*\[f\d+\]/g, '').trim();
const kept = [], dropped = [], seen = new Set();
for (const c of Array.isArray(o.changes) ? o.changes : []) {
  const label = String(c.fact || '').replace(/[\[\]\s]/g, '');
  const why = String(c.why || '').trim(), what = unlabel(c.what);
  const reason = !what || !why ? 'empty'
    : seen.has(what.toLowerCase()) ? 'duplicate'
    : c.type === 'update_fact' && !factById.has(label) ? `update_fact without an approved fact label (${label || 'none'})`
    : unknown(why).length ? `number in why not in the input (${unknown(why).join(', ')})`
    : !nums(why).length && !citesQuery(why) ? 'why cites no query or number'
    : unknown(what).length ? `number in what not in the page, data or facts (${unknown(what).join(', ')})` : '';
  if (reason) { dropped.push({ type: c.type, where: c.where, reason }); continue; }
  seen.add(what.toLowerCase());
  kept.push({ type: c.type, where: String(c.where || '').replace(/\s+/g, ' ').trim(), what, why,
    fact: c.type === 'update_fact' ? { id: label, text: factById.get(label) } : null });
}
const warnings = [];
const faq = (Array.isArray(o.faq) ? o.faq : []).filter(f => {
  const bad = unknown(`${f.q} ${f.a}`);
  if (bad.length) warnings.push(`FAQ "${String(f.q).slice(0, 60)}" dropped: number not in the input (${bad.join(', ')})`);
  return f.q && f.a && !bad.length;
}).map(f => ({ q: unlabel(f.q), a: unlabel(f.a) })).slice(0, 4);
let newTitle = String(o.new_title || '').trim(), newMeta = String(o.new_meta || '').trim();
if (unknown(newTitle).length) { warnings.push(`new title "${newTitle}" had a number not in the input; kept the current title`); newTitle = ''; }
if (unknown(newMeta).length) { warnings.push('new meta description had a number not in the input; left out'); newMeta = ''; }
if (!kept.length) return [{ json: { save: false, page: v.page, kept: 0, dropped,
  error: `every suggested change was dropped by the checks (${dropped.map(d => d.reason).join('; ').slice(0, 300)})` } }];
const LABEL = { add_section: 'Add a section', rewrite: 'Rewrite', update_fact: 'Update a fact', remove: 'Remove', add_faq: 'Add an FAQ' };
const fmt = n => n === null || n === undefined ? '–' : n;
const body = [
  `Refresh plan for ${v.page}`,
  `Google clicks: ${x.clicks_before} → ${x.clicks_now} (−${x.drop_pct}%), previous 28 days vs last 28 days.`,
  '', `**Diagnosis:** ${String(o.diagnosis || '').trim()}`,
  '', `**New title**${newTitle ? ` (${newTitle.length} chars): ${newTitle}` : `: keep "${v.title}"`}`,
  newMeta ? `**New meta description** (${newMeta.length} chars): ${newMeta}` : '**New meta description:** none suggested',
  '', '## Changes', '',
  ...kept.map((c, i) => [`${i + 1}. **${LABEL[c.type] || c.type}** — where: ${c.where}`,
    `   ${c.what.replace(/\n+/g, ' ')}`,
    ...(c.fact ? [`   Approved fact [${c.fact.id}]: ${c.fact.text}`] : []),
    `   _Why:_ ${c.why}`].join('\n')),
  ...(faq.length ? ['', '## FAQ (answer first)', '', ...faq.map(f => `**${String(f.q).trim()}**\n${String(f.a).trim()}\n`)] : []),
  '', '## Queries the page used to win (clicks, position: before → now)', '',
  ...x.top_queries.map(q => `- "${q.query}": clicks ${q.clicks_before} → ${q.clicks_now}, impressions ${q.impressions_before} → ${q.impressions_now}, position ${fmt(q.position_before)} → ${fmt(q.position_now)}`),
  ...(dropped.length || warnings.length ? ['', `_Checks: ${dropped.length} suggested change(s) dropped${dropped.length ? ` (${dropped.map(d => `${d.type}: ${d.reason}`).join('; ')})` : ''}${warnings.length ? '; ' + warnings.join('; ') : ''}._`] : []),
].join('\n');
// The quality gate sees only the words meant for the page; the page and the query data are
// its evidence, so a claim the page already makes is not flagged.
const gate_text = [newTitle, newMeta, ...kept.filter(c => c.type !== 'remove').map(c => c.what),
  ...faq.map(f => `${f.q} ${f.a}`)].filter(Boolean).join('\n\n');
const context = `Current page text:\n${x.page_text}\n\nSearch Console queries:\n` +
  x.top_queries.map(q => `${q.query}: clicks ${q.clicks_before} → ${q.clicks_now}`).join('\n');
return [{ json: { save: true, page: v.page, title: `Refresh: ${v.title}`.slice(0, 200), body, gate_text, context,
  note: `refresh of ${v.page} (clicks ${x.clicks_before} → ${x.clicks_now})`, kept: kept.length, dropped: dropped.length } }];
"""

REFRESH_AFTER_GATE = r"""
const d = $('Has plan?').first().json;
const g = $input.first().json;
const problems = Array.isArray(g.problems) ? g.problems : ['quality gate gave no result'];
return [{ json: { ...d, status: problems.length ? 'draft' : 'in_review',
  notes: [d.note, problems.length ? `Quality gate: ${problems.join('; ')}` : ''].filter(Boolean).join(' | ') } }];
"""

REFRESH_SUMMARY = r"""
const rows = $input.all().map(i => i.json);
const saved = rows.filter(r => r.item_id), failed = rows.filter(r => !r.item_id);
let notes = [];
try { notes = $('Pick pages').first().json.notes || []; } catch (e) {}
return [{ json: { notify: true, saved: saved.length, failed: failed.length, items: rows, text: [
  `*Content refresh*: ${saved.length} refresh plan${saved.length === 1 ? '' : 's'} saved to the calendar (channel blog). ` +
  'Edit the page in your CMS from the plan; nothing on the site is changed automatically.',
  ...saved.map(r => `- ${r.page} → calendar #${r.item_id} (${r.status}): ${r.kept} change(s) kept, ${r.dropped} dropped`),
  ...failed.map(r => `- ${r.page}: not refreshed, ${r.error}`),
  ...notes.map(n => `- ${n}`),
].join('\n') } }];
"""


def wf68():
    wf = Workflow(68, "Schedule · Content refresh")
    t = schedule(wf, "Mondays 07:00", "0 7 * * 1")
    h, cfg = gsc_gate(wf, "Content refresh")
    go = if_true(wf, "GSC ready?", "={{ $json.go }}")
    sy = gsc_sync(wf)
    dec = http(wf, "Declining pages", "GET", "={{ %s }}/pages/declining" % GSC,
               query={"min_clicks": "={{ $env.REFRESH_MIN_CLICKS || 20 }}", "drop": "={{ $env.REFRESH_MIN_DROP || 0.3 }}",
                      "limit": "20"}, never_error=True, full_response=True, timeout=30000)
    fa = http(wf, "Approved facts", "GET", "={{ $env.BRAND_URL }}/facts", never_error=True, continue_on_fail=True,
              full_response=True, timeout=10000, key=True)
    cal = http(wf, "Blog items", "GET", "={{ $env.CALENDAR_URL }}/items", query={"channel": "blog"}, key=True,
               full_response=True)
    pick = code(wf, "Pick pages", REFRESH_PICK)
    any_ = if_true(wf, "Any pages?", "={{ $json.pick }}")
    loop = loop_one_by_one(wf, "Loop")
    ex = http(wf, "Extract page", "POST", "={{ $env.EXTRACTOR_URL }}/extract", "={{ JSON.stringify({ url: $json.page }) }}",
              never_error=True, continue_on_fail=True, full_response=True, timeout=60000)
    pq = http(wf, "Page queries", "GET",
              "={{ %s }}/pages/{{ encodeURIComponent($('Loop').first(1).json.page) }}/queries" % GSC,
              query={"window": "previous", "limit": "10"}, never_error=True, continue_on_fail=True,
              full_response=True, timeout=30000)
    rv = code(wf, "Refresh vars", REFRESH_VARS)
    rd = if_true(wf, "Page read?", "={{ $json.ok }}")
    g = gateway(wf, "Draft refresh", "content_refresh", "$json.vars", continue_on_fail=True)
    ck = code(wf, "Check changes", REFRESH_CHECK)
    hp = if_true(wf, "Has plan?", "={{ $json.save }}")
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.gate_text }}", "channel": "blog",
                                               "context": "={{ $json.context }}", "ref": "={{ $json.page }}",
                                               "rewrite": "no", "links": "={{ $json.page }}"}, pos=[wf._x, 200])
    ag = code(wf, "After gate", REFRESH_AFTER_GATE, pos=[wf._x, 200])
    sv = http(wf, "Save item", "POST", "={{ $env.CALENDAR_URL }}/items",
              "={{ JSON.stringify({ title: $json.title, channel: 'blog_refresh', body: $json.body, status: $json.status, "
              "link: $json.page, notes: $json.notes }) }}", key=True)
    rs = code(wf, "Record saved", r"""
const d = $('After gate').first().json, s = $input.first().json;
return [{ json: { page: d.page, item_id: s.id, status: s.status, kept: d.kept, dropped: d.dropped } }];
""")
    rk = code(wf, "Record skipped", "const j = $input.first().json;\nreturn [{ json: { page: j.page, error: j.error } }];",
              pos=[wf._x, 520])
    sm = code(wf, "Summary", REFRESH_SUMMARY, pos=[wf._x, 100])
    rp = code(wf, "Report?", "// Not configured or a quiet week: no notification.\n"
                             "return " + NOTIFY_ON + " ? $input.all().filter(i => i.json.notify) : [];")
    n = notify(wf, "Notify", "$json.text", waits="$json.saved > 0")
    wf.chain(t, h, cfg, go)
    wf.link(go, sy, src_index=0)
    wf.link(go, rp, src_index=1)
    wf.chain(sy, dec, fa, cal, pick, any_)
    wf.link(any_, loop, src_index=0)
    wf.link(any_, rp, src_index=1)
    wf.link(loop, sm, src_index=0)      # done: every recorded page
    wf.link(loop, ex, src_index=1)      # one page per round (the local LLM is slow)
    wf.chain(ex, pq, rv, rd)
    wf.link(rd, g, src_index=0)
    wf.link(rd, rk, src_index=1)
    wf.chain(g, ck, hp)
    wf.link(hp, q, src_index=0)
    wf.link(hp, rk, src_index=1)
    wf.chain(q, ag, sv, rs)
    wf.link(rs, loop)
    wf.link(rk, loop)
    wf.chain(sm, rp, n)
    return wf, {
        "summary": "Every Monday it finds the pages that are losing Google clicks and drafts a refresh plan for each (refreshing old posts is capability #1 in the night-5 research: HubSpot doubled leads that way). It syncs Search Console (67 `POST /sync`, 28 days vs the 28 before) and reads `GET /pages/declining` (at least `REFRESH_MIN_CLICKS` clicks before and a `REFRESH_MIN_DROP` fall). A page with a calendar item noted `refresh of <url> (` created in the last 60 days is skipped, and at most `REFRESH_PER_WEEK` pages are refreshed per 7 days, most clicks lost first. One page at a time: it reads the live page (07 `/extract`, text cut to 12,000 characters) and the queries it used to win (67 `/pages/{url}/queries`, previous window, with current numbers beside), and the LLM (prompt `content_refresh`, with the approved facts) writes a diagnosis, a new title (≤ 60) and meta description (≤ 155), 3–8 concrete changes (`add_section`, `rewrite`, `update_fact`, `remove`, `add_faq`, each with where, what and a why) and up to 4 answer-first FAQs.\n\n**Checked in code, not trusted:** a change whose `why` has a number that is not in the input (the Search Console numbers, the page, the facts), or that cites neither a query nor a number, is dropped; so is a change whose `what` has an unknown number, and an `update_fact` that does not name an approved fact label (05 `/facts`). The words meant for the page then go through the quality gate (35, report only, the page text as evidence, the page URL as an allowed link). The plan is saved to the calendar (19) as channel `blog_refresh`, titled `Refresh: <page title>`, with the page as `link`: `in_review` when the gate passed, else `draft` with the problems in the notes. The notes start with `refresh of <url> (clicks X → Y)`, which is also how the next run knows it was done. The body is readable as it is: clicks before → now, diagnosis, new title and meta, the numbered changes with their reasons, the FAQ, the queries, and what the checks dropped.\n\n**Nothing on the site changes automatically.** A person edits the page in the CMS from the plan. The publisher (39) never sends `blog_refresh` items anywhere, even approved: approving just records that the plan was accepted. With `GSC_URL` empty, or gsc-sync not configured, the workflow finishes with a message and does nothing. A summary goes to `NOTIFY_WEBHOOK_URL`.",
        "schedule": "Mondays 07:00",
        "env": {"GSC_URL": "67-gsc-sync, e.g. `http://gsc-sync:8000`. Empty (or gsc-sync not configured) = the workflow does nothing",
                "REFRESH_PER_WEEK": "most pages refreshed per 7 days; default 3 (0 turns it off)",
                "REFRESH_MIN_CLICKS": "clicks a page needs in the previous 28 days to count; default 20",
                "REFRESH_MIN_DROP": "fall in clicks that counts as declining, as a fraction; default 0.3 (30 %)",
                "NOTIFY_WEBHOOK_URL": "optional; receives the summary"},
        "depends": ["67-gsc-sync", "07-page-extractor", "03-llm-gateway", "04-prompt-library (prompt `content_refresh`)",
                    "05-brand-service (approved facts)", "35-wf-tool-quality-gate", "19-content-calendar"],
    }


# --------------------------------------------------------------------------- 69 SEO opportunities

SEO_PICK = HELPERS + r"""
const sync = $('Sync').first().json;
const op = $('Opportunities').first().json;
const items = listOf($input.first().json);
const CAP = Math.floor(num($env.SEO_BRIEFS_PER_WEEK, 2));
const notes = [];
if (!ok(sync)) notes.push(`sync failed (${sync.statusCode || 'no response'}: ${errText(sync)}); used the last good sync`);
if (!ok(op) || !Array.isArray((op.body || {}).queries)) return [{ json: { pick: false, notify: true,
  text: `*SEO opportunities*: no query data from gsc-sync (${op.statusCode || 'no response'}: ${errText(op)}).`, notes } }];
// A query briefed in the last 90 days (calendar note: seo brief for "<query>") is not briefed again.
const key = s => String(s).trim().toLowerCase();
const now = Date.now(), recent = new Map();
let thisWeek = 0;
for (const i of items) {
  const age = (now - Date.parse(i.created_at || '')) / 864e5;
  const qs = [...String(i.notes || '').matchAll(/seo brief for "([^"]+)"/g)].map(m => key(m[1]));
  if (!qs.length) continue;
  if (age <= 7) thisWeek++;
  if (age <= 90) for (const q of qs) if (!recent.has(q)) recent.set(q, i.id);
}
const queries = op.body.queries.filter(q => q && q.query);
for (const q of queries.filter(q => recent.has(key(q.query)))) notes.push(`"${q.query}": briefed in the last 90 days (calendar #${recent.get(key(q.query))})`);
const todo = queries.filter(q => !recent.has(key(q.query)));
const left = Math.max(0, CAP - thisWeek);
const picked = todo.slice(0, left);   // gsc-sync sorts by impressions: the biggest opportunity first
if (todo.length > picked.length) notes.push(`${todo.length - picked.length} more query(ies) wait for next week (SEO_BRIEFS_PER_WEEK=${CAP}, ${thisWeek} briefed in the last 7 days)`);
if (picked.length) return picked.map(q => ({ json: { pick: true, query: String(q.query).replace(/"/g, ''),
  position: q.position, impressions: q.impressions, clicks: q.clicks, previous_position: q.previous_position,
  best_page: q.best_page ? q.best_page.page : null, notes } }));
return [{ json: { pick: false, notify: notes.length > 0, notes, text: [
  `*SEO opportunities*: nothing to brief this week (${queries.length} striking-distance quer${queries.length === 1 ? 'y' : 'ies'}).`,
  ...notes.map(n => `- ${n}`)].join('\n') } }];
"""

SEO_ITEM = HELPERS + r"""
const q = $('Loop').first(1).json;           // loop output 1 = the query of this round
const b = $input.first().json;
const brief = typeof b.result === 'string' ? b.result.trim() : '';
if (!brief) return [{ json: { save: false, query: q.query, error: `no brief from 29 (${errText(b) || 'sub-workflow error'})` } }];
const rel = $('Related queries').first().json;
const related = ok(rel) && Array.isArray((rel.body || {}).queries)
  ? rel.body.queries.filter(r => r.query && r.query.toLowerCase() !== q.query.toLowerCase()).slice(0, 10) : [];
const trend = q.previous_position == null ? 'new this window' : `${q.previous_position} in the 28 days before`;
const body = [
  `Striking-distance query from Search Console (last 28 days): "${q.query}"`,
  `Average position ${q.position} (${trend}), ${q.impressions} impressions, ${q.clicks} clicks.`,
  q.best_page ? `Our page that ranks for it: ${q.best_page}. Improve that page with this brief rather than starting a new post, unless the intent is different.`
              : 'Search Console shows no single page for it (anonymized query): a new post may be the way.',
  ...(related.length ? ['', 'Other queries that page gets (cover them too):',
    ...related.map(r => `- "${r.query}": position ${r.current.position ?? '–'}, ${r.current.impressions} impressions`)] : []),
  '', brief,
].join('\n');
return [{ json: { save: true, query: q.query, item: {
  title: `SEO brief: ${q.query}`.slice(0, 200), channel: 'seo_brief', status: 'idea', body, link: q.best_page || null,
  notes: `seo brief for "${q.query}" (pos ${q.position}, impressions ${q.impressions})` } } }];
"""

SEO_SUMMARY = r"""
const rows = $input.all().map(i => i.json);
const saved = rows.filter(r => r.item_id), failed = rows.filter(r => !r.item_id);
let notes = [];
try { notes = $('Pick queries').first().json.notes || []; } catch (e) {}
return [{ json: { notify: true, saved: saved.length, failed: failed.length, items: rows, text: [
  `*SEO opportunities*: ${saved.length} brief${saved.length === 1 ? '' : 's'} saved to the calendar as blog ideas.`,
  ...saved.map(r => `- "${r.query}" → calendar #${r.item_id}`),
  ...failed.map(r => `- "${r.query}": no brief, ${r.error}`),
  ...notes.map(n => `- ${n}`),
].join('\n') } }];
"""


def wf69():
    wf = Workflow(69, "Schedule · SEO opportunities")
    t = schedule(wf, "Wednesdays 07:00", "0 7 * * 3")
    h, cfg = gsc_gate(wf, "SEO opportunities")
    go = if_true(wf, "GSC ready?", "={{ $json.go }}")
    sy = gsc_sync(wf)
    op = http(wf, "Opportunities", "GET", "={{ %s }}/queries/opportunities" % GSC,
              query={"min_impressions": "={{ $env.SEO_MIN_IMPRESSIONS || 100 }}",
                     "min_position": "={{ $env.SEO_MIN_POSITION || 5 }}",
                     "max_position": "={{ $env.SEO_MAX_POSITION || 20 }}", "limit": "50"},
              never_error=True, full_response=True, timeout=30000)
    cal = http(wf, "Blog items", "GET", "={{ $env.CALENDAR_URL }}/items", query={"channel": "blog"}, key=True,
               full_response=True)
    pick = code(wf, "Pick queries", SEO_PICK)
    any_ = if_true(wf, "Any queries?", "={{ $json.pick }}")
    loop = loop_one_by_one(wf, "Loop")
    rel = http(wf, "Related queries", "GET",
               "={{ $json.best_page ? %s + '/pages/' + encodeURIComponent($json.best_page) + '/queries' : %s + '/health' }}"
               % (GSC, GSC), query={"window": "current", "limit": "11"},
               never_error=True, continue_on_fail=True, full_response=True, timeout=30000)
    # 29 audits competitor_url with 12; our own ranking page there gives a brief that improves
    # it (67 README, "Contract for workflows").
    br = call_workflow(wf, "SEO brief", 29, {"keyword": "={{ $('Loop').first(1).json.query }}",
                                             "competitor_url": "={{ $('Loop').first(1).json.best_page || '' }}",
                                             "audience": "={{ $env.SEO_AUDIENCE || '' }}"})
    next(n for n in wf.nodes if n["name"] == br)["onError"] = "continueRegularOutput"
    it = code(wf, "Brief item", SEO_ITEM)
    hb = if_true(wf, "Has brief?", "={{ $json.save }}")
    sv = http(wf, "Save item", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json.item) }}", key=True)
    rs = code(wf, "Record saved", r"""
const d = $('Has brief?').first().json, s = $input.first().json;
return [{ json: { query: d.query, item_id: s.id, status: s.status } }];
""")
    rk = code(wf, "Record skipped", "const j = $input.first().json;\nreturn [{ json: { query: j.query, error: j.error } }];",
              pos=[wf._x, 520])
    sm = code(wf, "Summary", SEO_SUMMARY, pos=[wf._x, 100])
    rp = code(wf, "Report?", "// Not configured or a quiet week: no notification.\n"
                             "return " + NOTIFY_ON + " ? $input.all().filter(i => i.json.notify) : [];")
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, h, cfg, go)
    wf.link(go, sy, src_index=0)
    wf.link(go, rp, src_index=1)
    wf.chain(sy, op, cal, pick, any_)
    wf.link(any_, loop, src_index=0)
    wf.link(any_, rp, src_index=1)
    wf.link(loop, sm, src_index=0)
    wf.link(loop, rel, src_index=1)     # one query per round (29 makes a slow LLM call)
    wf.chain(rel, br, it, hb)
    wf.link(hb, sv, src_index=0)
    wf.link(hb, rk, src_index=1)
    wf.chain(sv, rs)
    wf.link(rs, loop)
    wf.link(rk, loop)
    wf.chain(sm, rp, n)
    return wf, {
        "summary": "Every Wednesday it turns Search Console's striking-distance queries into SEO briefs. It syncs 67 (`POST /sync`) and reads `GET /queries/opportunities`: queries with at least `SEO_MIN_IMPRESSIONS` impressions in the last 28 days at an average position between `SEO_MIN_POSITION` and `SEO_MAX_POSITION`, biggest first, each with our page that ranks best for it. A query with a calendar item noted `seo brief for \"<query>\"` created in the last 90 days is skipped, and at most `SEO_BRIEFS_PER_WEEK` briefs are made per 7 days. One query at a time, it calls the SEO brief tool (29) with the query as `keyword` and our ranking page as `competitor_url` (29 audits it with 12, so the brief improves the page that already ranks, as the 67 README suggests). 29 only returns markdown, so this workflow saves it: a calendar item (19) with channel `seo_brief` (never published by 39), status `idea`, title `SEO brief: <query>`, our page as `link`, notes `seo brief for \"<query>\" (pos P, impressions I)`. The body starts with the Search Console numbers (position and its trend, impressions, clicks), our page, the other queries that page gets (67 `/pages/{url}/queries`), then the brief. Ask the chat agent to write from it, or improve the page by hand. With `GSC_URL` empty, or gsc-sync not configured, the workflow finishes with a message and does nothing. A summary goes to `NOTIFY_WEBHOOK_URL`.",
        "schedule": "Wednesdays 07:00",
        "env": {"GSC_URL": "67-gsc-sync, e.g. `http://gsc-sync:8000`. Empty (or gsc-sync not configured) = the workflow does nothing",
                "SEO_BRIEFS_PER_WEEK": "most briefs per 7 days; default 2 (0 turns it off)",
                "SEO_MIN_IMPRESSIONS": "impressions a query needs in the last 28 days; default 100",
                "SEO_MIN_POSITION": "best average position that still counts as an opportunity; default 5",
                "SEO_MAX_POSITION": "worst average position that counts; default 20",
                "SEO_AUDIENCE": "optional audience passed to 29",
                "NOTIFY_WEBHOOK_URL": "optional; receives the summary"},
        "depends": ["67-gsc-sync", "29-wf-tool-seo-brief", "19-content-calendar"],
    }


ALL_P6 = [wf68, wf69]
