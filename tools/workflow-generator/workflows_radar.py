"""Competitor radar: 37 (competitor watch, with ads from 78) and 81 (track_competitor chat tool).

78-ad-library-sync is the competitor registry: an active competitor's key pages become 09
watches there. 37 reads 09's page diffs plus 78's ads and manual-check links; 81 lets the chat
agent add a competitor from its homepage. Official ad APIs only; nothing here scrapes a
platform (see 78-ad-library-sync/README.md).
"""
from n8nlib import (GATE_NOTIFY, Workflow, code, gateway, http, if_true, notify, schedule,
                    sub_trigger)

AD = "$env.AD_LIBRARY_URL.replace(/\\/+$/, '')"
# AD_LIBRARY_URL empty: call 09's /health instead (ignored in code), so the node never fails.
AD_OR = "={{ $env.AD_LIBRARY_URL ? %s + '%s' : $env.MONITOR_URL + '/health' }}"
SOFT = dict(never_error=True, continue_on_fail=True, full_response=True)

HELPERS = r"""
const errText = r => { r = r || {}; const b = r.body; const e = (b && typeof b === 'object' ? (b.detail ?? b.message) : b) ?? r.error?.message ?? r.error; return e == null || e === '' ? '' : String(typeof e === 'object' ? JSON.stringify(e) : e).slice(0, 200); };
const clean = s => String(s ?? '').replace(/\s+/g, ' ').trim();
"""

# ---------------------------------------------------------------------------- 37 competitor watch

# Pure: tests run it (tests/competitor_watch_test.js). Page diffs from 09, ads and links from 78.
# Goes on when a page changed or an ad is new/changed/stopped since the last run (6 h).
WATCH_CHANGES = HELPERS + r"""
const AD = String($env.AD_LIBRARY_URL || '').trim();
const changed = $('Check pages').first().json.changed || [];
const bodyOf = n => { const r = $(n).first().json || {}; return r.statusCode >= 200 && r.statusCode < 300 ? r.body : null; };
const adsBody = AD ? bodyOf('New ads') : null;
const ads = adsBody && Array.isArray(adsBody.ads) ? adsBody.ads.filter(a => a && a.ad_id) : [];
const linksBody = AD ? bodyOf('Ad library links') : null;
const links = Array.isArray(linksBody) ? linksBody.filter(c => c && Array.isArray(c.links)) : [];
const sync = AD ? ($('Sync ads').first().json || {}) : {};
const ad_note = !AD ? '' : sync.statusCode === 200 ? '' : `Ad library sync did not run: ${errText(sync) || 'no answer'} (showing stored ads).`;
const since = Date.now() - 6 * 3600 * 1000;
const fresh = ads.filter(a => [a.first_seen, a.changed_at, a.stopped_seen_at].some(t => t && Date.parse(t) >= since));
if (!changed.length && !fresh.length) return [];
// Every text 78 returned, exact: the digest may quote only these (or the page diffs).
const ad_texts = [...new Set(ads.flatMap(a => [...(a.texts || []), ...(a.link_titles || [])]).map(clean).filter(Boolean))];
const ads_text = ads.slice(0, 20).map(a => `- ${a.competitor} (${a.change}, first seen ${String(a.first_seen).slice(0, 10)}): ` +
  ((a.texts || []).length ? a.texts.slice(0, 2).map(t => `"${clean(t)}"`).join(' / ') : '(no text: image or video ad)')).join('\n');
// "They launched X": added lines that announce something new.
const LAUNCH = /\b(new|introducing|launch(es|ed|ing)?|now available|just released|coming soon|meet the)\b/i;
const launches = [];
for (const c of changed) for (const line of String(c.diff || '').split('\n'))
  if (line.startsWith('+') && !line.startsWith('+++') && LAUNCH.test(line))
    launches.push({ label: c.label || c.url, url: c.url, line: clean(line.slice(1)).slice(0, 200) });
// Pricing pages: 78 labels them "<name> · pricing"; hand-made watches are matched by URL.
const isPricing = c => /·\s*pricing$/i.test(c.label || '') || /\/(pricing|plans?|prices?|preise|tarife?s?|abo|subscriptions?)(\/|$|\?|\.)/i.test(c.url || '');
const pricing = changed.filter(isPricing).map(c => ({ competitor: String(c.label || c.url).split(' · ')[0], url: c.url, diff: String(c.diff).slice(0, 3000) }));
return [{ json: {
  date: new Date().toISOString().slice(0, 10),
  changes: changed.length ? changed.map(c => `### ${c.label || c.url} (${c.url})\n${String(c.diff).slice(0, 1500)}`).join('\n\n')
                          : '(No watched page changed. Only the ads below are new.)',
  ads_text, ad_texts, ads: ads.slice(0, 15), fresh: fresh.length, launches, pricing, links, ad_note,
} }];
"""

# Pure: every quote in the digest must be an exact ad text from 78 or an exact part of the page
# diffs; any other quote (3+ words) is replaced. Sections after the LLM's text are built here.
WATCH_DIGEST = HELPERS + r"""
const d = $('Changes').first().json;
const norm = s => clean(s).replace(/[“”„]/g, '"').replace(/[‘’]/g, "'");
const sources = [...(d.ad_texts || []), ...String(d.changes || '').split('\n').map(l => l.replace(/^[+-]/, ''))].map(norm).filter(Boolean);
let removed = 0;
const checkQuotes = text => String(text || '').replace(/"([^"\n]{2,800})"|“([^”\n]{2,800})”/g, (m, a, b) => {
  const q = norm(a ?? b).replace(/^[.…\s]+|[.…\s]+$/g, '');
  if (q.split(' ').length < 3 || sources.some(s => s.includes(q))) return m;
  removed++;
  return '[quote removed: not an exact ad or page text]';
});
// The model's text is checked; the sections below are built from 78's and 09's data, exact by construction.
const lines = [checkQuotes(String($input.first().json.output || '').trim())];
if ((d.launches || []).length) lines.push('', '### Possible launches (check the page)',
  ...d.launches.slice(0, 10).map(l => `- ${l.label}: "${l.line}" (${l.url})`));
if ((d.ads || []).length) lines.push('', '### New, changed or stopped ads this week (Meta Ad Library, EU)',
  ...d.ads.map(a => `- ${a.competitor}, ${a.change} (first seen ${String(a.first_seen).slice(0, 10)}): ` +
    ((a.texts || []).length ? `"${clean(a.texts[0])}"` : '(no text: image or video ad)') + ` ${a.library_url}`));
if (d.ad_note) lines.push('', `_${d.ad_note}_`);
const manual = (d.links || []).slice(0, 10).map(c => {
  const seen = new Set();
  const ls = c.links.filter(l => !l.api && (l.platform !== 'meta' || l.market === 'ALL'))
    .filter(l => !seen.has(l.platform) && seen.add(l.platform))
    .map(l => `[${l.platform === 'meta' ? 'Meta (all countries)' : l.platform[0].toUpperCase() + l.platform.slice(1)}](${l.url})`);
  return ls.length ? `- ${c.competitor}: ${ls.join(' · ')}` : '';
}).filter(Boolean);
if (manual.length) lines.push('', '### Check by hand (no API, or outside the EU)', ...manual);
let digest = lines.join('\n');
if (removed) digest += `\n\n_${removed} quote(s) removed: not an exact ad text (78) or page text (09)._`;
return [{ json: { digest, removed_quotes: removed, pricing: d.pricing || [] } }];
"""

WATCH_PRICING = r"""
const fr = $input.first().json || {};
const facts = Array.isArray((fr.body || {}).facts) ? fr.body.facts : [];
const factsText = facts.map(f => `- ${String(f.text).trim()}`).join('\n');
return ($('Digest').first().json.pricing || []).slice(0, 3).map(p => ({ json: { ...p, facts: factsText } }));
"""

# Pure: the model's brief is checked here, not trusted. A line with a number that is in neither
# the diff nor the approved facts is dropped (thousands separators ignored).
WATCH_BRIEF = r"""
const src = $('Pricing changes').all().map(i => i.json);
const nums = s => (String(s || '').match(/\d[\d,.]*/g) || []).map(n => n.replace(/[,.]$/, '').replace(/,(?=\d{3}\b)/g, ''));
const out = [];
$input.all().forEach((it, i) => {
  const x = src[i] || src[0] || {};
  const b = it.json.output;
  if (!b || typeof b !== 'object' || !['match', 'counter', 'ignore'].includes(b.decision)) return;
  const known = new Set(nums(`${x.diff}\n${x.facts}`));
  let dropped = 0;
  const ok = s => { const bad = nums(s).filter(n => !known.has(n)); if (bad.length) dropped++; return !bad.length; };
  const reasons = (b.reasons || []).map(String).filter(ok);
  const points = (b.response_points || []).map(String).filter(ok);
  const what = ok(b.what_changed) ? String(b.what_changed) : 'See the diff below.';
  const body = [`**${x.competitor}** changed its pricing page: ${x.url}`, '', `**What changed:** ${what}`,
    `**Suggested decision:** ${b.decision}`, '', '**Why:**', ...(reasons.length ? reasons.map(r => `- ${r}`) : ['- (no reason left after the number check)']),
    '', '**What we could do:**', ...(points.length ? points.map(p => `- ${p}`) : ['- nothing for now']),
    '', '**Diff** (`-` before, `+` after):', '```diff', x.diff, '```',
    ...(dropped ? ['', `_${dropped} line(s) dropped: a number not in the diff or our approved facts._`] : []),
    '', '_Drafted by the competitor watch (37) from the approved facts (05). A brief for the team, never published._'].join('\n');
  out.push({ json: { title: `Competitor brief: ${x.competitor} pricing (${b.decision})`.slice(0, 200), channel: 'competitor_brief',
    status: 'idea', link: x.url, body, notes: `competitor brief for ${x.url} (${b.decision})` } });
});
return out;
"""

# Pure: tests run it (tests/competitor_radar_test.js). 78 built this month's positioning map
# (every quote already checked there); this turns it into a KB document, a calendar idea
# (channel `positioning`, never published) and a short notification. AD_LIBRARY_URL empty: nothing.
POSITIONING_REPORT = HELPERS + r"""
if (!String($env.AD_LIBRARY_URL || '').trim()) return [];
const r = $input.first().json || {};
const s = r.body;
if (!(r.statusCode >= 200 && r.statusCode < 300) || !s || typeof s !== 'object' || !s.month) {
  return [{ json: { ok: false, notify: `*Positioning map*\nThe monthly build failed: ${errText(r) || 'no answer'}. Nothing was saved.` } }];
}
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const [y, m] = String(s.month).split('-');
const label = `${MONTHS[Number(m) - 1] || m} ${y}`;
const ws = (s.white_space || []).map(w => `- ${w.theme}` + ((w.facts || [])[0] ? `: "${clean(w.facts[0].quote)}" (${w.facts[0].source.ref})` : ''));
const crowded = (s.crowded || []).map(c => `- ${c.theme}: ${(c.competitors || []).join(', ')}`);
const shifts = (s.shifts || []).slice(0, 5).map(x => `- ${x.text}`);
const failed = (s.brands || []).filter(b => b.error).map(b => b.name);
const notify = [`*Positioning map ${label}*`,
  '', '*White space* (no competitor claims it; our approved facts back it):', ...(ws.length ? ws : ['- none']),
  '', '*Crowded:*', ...(crowded.length ? crowded : ['- none']),
  '', `*Shifts${s.previous_month ? ' since ' + s.previous_month : ''}:*`, ...(shifts.length ? shifts : ['- none']),
  ...(failed.length ? ['', `_Not read: ${failed.join(', ')}._`] : []),
  '', 'Full map: control room → More → Positioning.'].join('\n');
return [{ json: { ok: true, notify,
  doc: { doc_id: `positioning-${s.month}`, title: `Positioning map ${label}`, text: String(s.markdown || ''), source: 'positioning-map' },
  idea: { title: `Positioning map ${label}: ${ws.length} white-space theme(s), ${shifts.length} shift(s)`.slice(0, 200),
    channel: 'positioning', status: 'idea', body: String(s.markdown || '') + '\n\n_Built by the competitor watch (37) from 78\'s positioning map. A brief for the team, never published._',
    notes: `positioning map #${s.id} (${s.month})` } } }];
"""


def wf37():
    wf = Workflow(37, "Schedule · Competitor watch")
    t = schedule(wf, "Every 6 hours", "0 */6 * * *")
    c = http(wf, "Check pages", "POST", "={{ $env.MONITOR_URL }}/check", "={{ JSON.stringify({}) }}", key=True)
    sy = http(wf, "Sync ads", "POST", AD_OR % (AD, "/sync"), key=True, timeout=120000, **SOFT)
    na = http(wf, "New ads", "GET", AD_OR % (AD, "/ads"), timeout=30000,
              query={"since": "={{ new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10) }}"}, **SOFT)
    li = http(wf, "Ad library links", "GET", AD_OR % (AD, "/links"), timeout=30000, **SOFT)
    p = code(wf, "Changes", WATCH_CHANGES)
    g = gateway(wf, "Explain changes", "competitor_changes", "{ changes: $json.changes, ads: $json.ads_text || null }")
    dg = code(wf, "Digest", WATCH_DIGEST)
    kb = http(wf, "Save to knowledge base", "POST", "={{ $env.KB_URL }}/docs",
              "={{ JSON.stringify({ doc_id: 'competitors-' + $('Changes').first().json.date + '-' + Date.now(), title: 'Competitor changes ' + $('Changes').first().json.date, text: $json.digest, source: 'competitor-watch' }) }}",
              key=True, continue_on_fail=True)
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "'*Competitor watch*\\n' + $('Digest').first().json.digest")
    wf.chain(t, c, sy, na, li, p, g, dg, kb, gn, n)
    # Pricing page changed: a response brief (match / counter / ignore) as a calendar idea.
    x0 = wf._x - 240 * 3
    fa = http(wf, "Approved facts", "GET", "={{ $env.BRAND_URL }}/facts", timeout=10000, pos=[x0, 520], **SOFT, key=True)
    pc = code(wf, "Pricing changes", WATCH_PRICING, pos=[x0 + 240, 520])
    gb = gateway(wf, "Draft brief", "competitor_brief",
                 "{ competitor: $json.competitor, url: $json.url, diff: $json.diff, facts: $json.facts || null }",
                 pos=[x0 + 480, 520], continue_on_fail=True)
    cb = code(wf, "Check brief", WATCH_BRIEF, pos=[x0 + 720, 520])
    sv = http(wf, "Save brief", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json) }}",
              key=True, continue_on_fail=True, pos=[x0 + 960, 520])
    wf.link(dg, fa)
    wf.chain(fa, pc, gb, cb, sv)
    # Monthly (1st, 07:00): the positioning map from 78 -> KB document, calendar idea, notification.
    y = 820
    mt = schedule(wf, "Monthly positioning", "0 7 1 * *")
    wf.nodes[-1]["position"] = [0, y]
    pb = http(wf, "Build positioning", "POST", AD_OR % (AD, "/positioning/build"), key=True,
              timeout=1800000, pos=[240, y], **SOFT)
    pm = code(wf, "Positioning map", POSITIONING_REPORT, pos=[480, y])
    ok = if_true(wf, "Built?", "={{ $json.ok }}", pos=[720, y])
    pk = http(wf, "Save map to knowledge base", "POST", "={{ $env.KB_URL }}/docs",
              "={{ JSON.stringify($('Positioning map').first().json.doc) }}", key=True, continue_on_fail=True, pos=[960, y])
    pi = http(wf, "Save positioning idea", "POST", "={{ $env.CALENDAR_URL }}/items",
              "={{ JSON.stringify($('Positioning map').first().json.idea) }}", key=True, continue_on_fail=True,
              pos=[1200, y])
    pg = code(wf, "Positioning webhook set?", GATE_NOTIFY, pos=[1440, y])
    pn = notify(wf, "Notify positioning", "$('Positioning map').first().json.notify", pos=[1680, y])
    wf.chain(mt, pb, pm, ok, pk, pi, pg, pn)
    wf.link(ok, pg, src_index=1)   # the build failed: say so, save nothing
    return wf, {
        "summary": (
            "Every 6 hours it diffs the competitor pages you watch (09) and, when `AD_LIBRARY_URL` is set, syncs "
            "the competitors' ads from the official Meta Ad Library API (78 `POST /sync`, EU-delivered ads only) "
            "and reads the week's new, changed or stopped ads (78 `GET /ads`) and the manual-check links "
            "(78 `GET /links`). It goes on when a page changed or an ad appeared, changed or stopped since the "
            "last run. The LLM (prompt `competitor_changes`, with the ad texts) explains what changed and "
            "whether to react.\n\n**Checked in code, not trusted:** every quote in the digest must be an exact "
            "ad text from 78 or an exact part of a page diff; any other quote of 3+ words is replaced by "
            "`[quote removed: ...]` and counted. The code then adds: *Possible launches* (added lines that say "
            "new / introducing / launched / now available ...), *New, changed or stopped ads this week* (each "
            "ad's exact text and its Ad Library link) and *Check by hand* (Meta for all countries, Google Ads "
            "Transparency Center, LinkedIn and TikTok ad libraries: no API or outside the EU, so a person opens "
            "them; nothing is scraped). The digest is saved to the knowledge base and posted to your webhook.\n\n"
            "**Pricing page changed** (78 labels it `<name> · pricing`, or the URL has /pricing, /plans ...): "
            "the LLM (prompt `competitor_brief`, with the approved facts from 05) suggests match, counter or "
            "ignore, with reasons and response points. Lines with a number that is in neither the diff nor the "
            "facts are dropped. The brief is saved to the calendar (19) as an `idea` with channel "
            "`competitor_brief`, which the publisher (39) never sends. At most 3 briefs per run.\n\n"
            "With `AD_LIBRARY_URL` empty, or 78 down, it works as before on page changes only.\n\n"
            "**Monthly positioning map** (1st of the month, 07:00, needs `AD_LIBRARY_URL`): 78 "
            "`POST /positioning/build` sorts what each active competitor says (their watched pages' latest "
            "text in 09 and their active ads) and what we say (approved facts from 05, our own watched pages) "
            "into messaging themes (prompt `positioning_themes`); 78 keeps only quotes that are an exact part "
            "of their source. The map (themes x brands, white space = no competitor claims it AND one of our "
            "approved facts backs it, crowded themes, shifts since last month) is saved to the knowledge base "
            "(doc `positioning-<YYYY-MM>`), to the calendar as an `idea` with channel `positioning` (the "
            "publisher never sends it), and the white space, crowded themes and top shifts are posted to your "
            "webhook. The control room shows it under More → Positioning. A failed build is reported and saves "
            "nothing."),
        "schedule": "every 6 hours; positioning map on the 1st of each month at 07:00",
        "env": {"NOTIFY_WEBHOOK_URL": "optional",
                "AD_LIBRARY_URL": "78-ad-library-sync, e.g. `http://ad-library-sync:8000`. Empty = pages only, no ads or links"},
        "setup": ("Add competitors in 78 (their key pages become 09 watches automatically), or ask the chat agent "
                  "\"track competitor https://rival.example.com\" (tool 81):\n\n```bash\n"
                  "curl -X POST localhost:8178/competitors -H \"X-API-Key: $INTERNAL_API_KEY\" \\\n"
                  "  -H 'content-type: application/json' -d '{\"name\":\"Rival Beans\",\"website\":\"https://rival.example.com/\","
                  "\"key_pages\":[{\"url\":\"https://rival.example.com/pricing\",\"type\":\"pricing\"}],\"meta_page_id\":\"123456789\"}'\n```\n\n"
                  "Pages can still be watched by hand in 09 (`POST localhost:8109/watches`)."),
        "depends": ["03-llm-gateway", "05-brand-service", "06-knowledge-base", "09-change-monitor",
                    "19-content-calendar", "78-ad-library-sync (optional)"],
    }


# ---------------------------------------------------------------------------- 81 track competitor

# Pure: tests run it. The homepage's internal links (07 list_links) are the only candidates.
TRACK_CANDIDATES = HELPERS + r"""
const s = $('Start').first().json;
const r = $input.first().json || {};
const b = r.body && typeof r.body === 'object' ? r.body : {};
const raw = String(s.url || '').trim();
const fail = text => [{ json: { go: false, result: text } }];
if (!$env.AD_LIBRARY_URL) return fail('Competitor tracking needs the ad-library-sync service (78): AD_LIBRARY_URL is not set on n8n. Nothing was added.');
if (!/^https?:\/\/[^\s/]+\.[^\s/]+/i.test(raw)) return fail(`"${raw}" is not a website address (https://...). Nothing was added.`);
if (!(r.statusCode >= 200 && r.statusCode < 300) || !b.url) return fail(`Could not read ${raw}: ${errText(r) || 'no answer from the page extractor'}. Nothing was added.`);
const host = u => { try { return new URL(u).hostname.toLowerCase().replace(/^www\./, ''); } catch (e) { return ''; } };
const home = String(b.url);
const h = host(home);
const JUNK = /\/(login|log-in|signin|sign-in|signup|register|account|cart|basket|checkout|privacy|terms|legal|imprint|impressum|cookies?|careers|jobs|contact|search|wp-admin)(\/|$|\?)/i;
const links = (b.link_list || []).map(l => ({ url: String(l.url || '').split('#')[0], text: clean(l.text) }))
  .filter(l => host(l.url) === h && /^https?:/i.test(l.url) && !JUNK.test(l.url) && l.url.replace(/\/$/, '') !== home.replace(/\/$/, ''));
const uniq = [...new Map(links.map(l => [l.url, l])).values()].slice(0, 80);
const og = b.og || {};
const name = clean(s.name) || clean(og.site_name) || clean(String(b.title || '').split(/\s[|\-–—:·]\s/)[0]) || h;
return [{ json: { go: true, home, host: h, name: name.slice(0, 120), candidates: uniq.map(l => l.url),
  links: uniq.map(l => l.text ? `${l.url} (${l.text})` : l.url).join('\n'),
  markets: String(s.markets || '').toUpperCase().split(/[\s,;]+/).filter(m => /^[A-Z]{2}$/.test(m)) } }];
"""

# Pure: keep only URLs the model copied exactly from the candidate list, one per type order.
TRACK_PICK = r"""
const c = $('Candidates').first().json;
const out = $input.first().json.output;
const TYPES = ['pricing', 'product', 'features', 'about'];
const allowed = new Set(c.candidates);
const picked = [], seen = new Set();
let rejected = 0;
for (const p of (out && Array.isArray(out.pages) ? out.pages : [])) {
  const url = String(p && p.url || '').trim();
  if (!allowed.has(url) || !TYPES.includes(p.type)) { rejected++; continue; }
  if (seen.has(url)) continue;
  seen.add(url);
  picked.push({ url, type: p.type });
}
const pages = picked.slice(0, 5);
// One item per page for the reachability check; none picked: check the homepage only.
return (pages.length ? pages : [{ url: c.home, type: 'home', noop: true }]).map(p => ({ json: { ...p, rejected } }));
"""

TRACK_READABLE = r"""
const picks = $('Pick pages').all().map(i => i.json);
const c = $('Candidates').first().json;
const key_pages = [], unreadable = [];
$input.all().forEach((it, i) => {
  const p = picks[i];
  if (!p || p.noop) return;
  const r = it.json || {};
  if (r.statusCode >= 200 && r.statusCode < 300 && r.body && String(r.body.text || '').trim()) key_pages.push({ url: p.url, type: p.type });
  else unreadable.push(p.url);
});
const body = { name: c.name, website: c.home, key_pages, status: 'active', notes: 'added by the chat agent (81)' };
if (c.markets.length) body.markets = c.markets;
return [{ json: { body, unreadable, rejected: (picks[0] || {}).rejected || 0 } }];
"""

TRACK_RESULT = HELPERS + r"""
const r = $input.first().json || {};
const k = $('Readable pages').first().json;
if (!(r.statusCode >= 200 && r.statusCode < 300) || !r.body || !r.body.id) {
  return [{ json: { result: `Could not save the competitor in 78: ${errText(r) || 'no answer'}. Nothing was added.` } }];
}
const c = r.body, w = c.watch_state || {};
const lines = [`Now tracking **${c.name}** (${c.website}).`, ''];
lines.push('Watched pages (checked every 6 hours by the competitor watch):');
lines.push(...((w.watches || []).length ? w.watches.map(x => `- ${x.label}: ${x.url}`) : ['- none']));
if (w.error) lines.push('', `Watches could not be set up: ${w.error}`);
for (const e of w.errors || []) lines.push(`- not watched: ${e.url} (${e.error})`);
if (k.unreadable.length) lines.push('', `Skipped (could not be read): ${k.unreadable.join(', ')}`);
if (k.rejected) lines.push(`${k.rejected} suggested page(s) were not links on the homepage and were ignored.`);
lines.push('', c.meta_page_id ? `Meta ads: page ${c.meta_page_id}, EU markets ${(c.markets.length ? c.markets : ['default']).join(', ')}.`
  : 'Meta ads: searched by name until you add the Facebook page id (`meta_page_id`, from the page\'s About > Page transparency).');
lines.push(`Other ad libraries (Google, LinkedIn, TikTok) have no API: open them by hand from 78, GET /links/${encodeURIComponent(c.name)}.`);
return [{ json: { result: lines.join('\n') } }];
"""


def wf81():
    wf = Workflow(81, "Tool · Track a competitor")
    s = sub_trigger(wf, [("url", "string"), ("name", "string"), ("markets", "string")])
    x = http(wf, "Fetch homepage", "POST", "={{ $env.EXTRACTOR_URL }}/extract",
             "={{ JSON.stringify({ url: String($json.url || '').trim(), list_links: true }) }}", timeout=60000, **SOFT)
    cd = code(wf, "Candidates", TRACK_CANDIDATES)
    go = if_true(wf, "Can go on?", "={{ $json.go }}")
    g = gateway(wf, "Propose pages", "competitor_key_pages", "{ url: $json.home, title: $json.name, links: $json.links || '(none)' }",
                continue_on_fail=True)
    pk = code(wf, "Pick pages", TRACK_PICK)
    rd = http(wf, "Readable?", "POST", "={{ $env.EXTRACTOR_URL }}/extract", "={{ JSON.stringify({ url: $json.url }) }}",
              timeout=30000, **SOFT)
    kp = code(wf, "Readable pages", TRACK_READABLE)
    cr = http(wf, "Save competitor", "POST", "={{ %s }}/competitors" % AD, "={{ JSON.stringify($json.body) }}",
              key=True, query={"upsert": "true"}, timeout=60000, **SOFT)
    rs = code(wf, "Result", TRACK_RESULT)
    stop = code(wf, "Not added", "return [{ json: { result: $input.first().json.result } }];", pos=[wf._x - 240 * 6, 500])
    wf.chain(s, x, cd, go)
    wf.link(go, g, src_index=0)
    wf.link(go, stop, src_index=1)
    wf.chain(g, pk, rd, kp, cr, rs)
    return wf, {
        "summary": (
            "Adds a competitor from its homepage. It reads the homepage with the page extractor (07, "
            "`list_links: true`), and the LLM (prompt `competitor_key_pages`) picks at most 5 key pages "
            "(pricing, product, features, about) **from that page's own links**. Checked in code: a URL "
            "is kept only if it is exactly one of the homepage's links on the same domain (login, cart, "
            "legal, careers, contact pages are never candidates) and 07 can read it. The competitor is "
            "saved in the registry (78 `POST /competitors?upsert=true`, status `active`); 78 turns the "
            "homepage and the key pages into 09 watches with default filters (dates, cookie lines, "
            "\"Only 3 left\" counters ignored). Running it again for the same site updates it. The answer "
            "lists what is watched and how ads are covered (Meta API for EU markets; Google, LinkedIn "
            "and TikTok as links to open by hand). With `AD_LIBRARY_URL` empty it adds nothing and says why."),
        "inputs": {"url": "the competitor's homepage", "name": "optional display name",
                   "markets": "optional 2-letter countries, comma-separated (default: 78's AD_COUNTRIES)"},
        "output": "`{result}`: markdown confirmation",
        "env": {"AD_LIBRARY_URL": "78-ad-library-sync, e.g. `http://ad-library-sync:8000`"},
        "depends": ["03-llm-gateway", "07-page-extractor", "78-ad-library-sync", "09-change-monitor (via 78)"],
        "called_by": "the chat agent (24), tool `track_competitor`",
        "test": {"url": "https://example.com", "name": "", "markets": ""},
    }


ALL_RADAR = [wf81]


# ---------------------------------------------------------------------------- 41 weekly report line

# Pure: one short section for the weekly report from 78 POST /suggestions/scan.
WEEKLY_SUGGESTIONS = r"""
const prev = $('Experiments section').first().json;
const r = $input.first().json || {};
const b = $env.AD_LIBRARY_URL && r.statusCode >= 200 && r.statusCode < 300 && r.body && typeof r.body === 'object' ? r.body : null;
if (!b || !b.pending) return [{ json: prev }];
const fresh = (b.new || []).map(n => `${n.name} (${n.mentions} mentions)`);
const lines = ['### Competitor suggestions',
  `${b.pending} website(s) our reviews, social mentions or trend digests keep naming are waiting for you` +
  (fresh.length ? `; new this week: ${fresh.slice(0, 5).join(', ')}` : '') + '.',
  'See them with the quotes: `GET /suggestions` on ad-library-sync (78). Track one: ' +
  '`POST /competitors/<name>/status {"status":"active"}`; drop it: `{"status":"ignored"}`.'];
return [{ json: { ...prev, markdown: [prev.markdown, lines.join('\n')].filter(Boolean).join('\n\n') } }];
"""
