"""Phase 5 workflows (66): the weekly newsletter; plus the code of 41's "next actions" section."""
from n8nlib import GATE_NOTIFY, Workflow, call_workflow, code, gateway, http, if_true, notify, schedule

# --------------------------------------------------------------------------- 41 next actions (used in workflows.wf41)

WEEKLY_ACTION_DATA = r"""
// One compact JSON for the weekly_actions prompt. Every part is optional: a source that
// failed (or is not configured) is left out, never invented.
const lw = $('Last week').first().json;
const body = n => { try { const b = $(n).first().json.body; return b && typeof b === 'object' ? b : null; } catch (e) { return null; } };
const data = {};
const k = $('KPIs').first().json;
if (k && k.totals) {
  data.kpis = { totals: k.totals, delta_pct: k.delta_pct || null,
    by_channel: (k.by_channel || []).map(c => ({ channel: c.channel, sessions: c.sessions, clicks: c.clicks,
      conversions: c.conversions, cvr: c.cvr })) };
}
const ins = body('Clicks by channel');
if (ins && ((ins.by_channel || []).length || (ins.top_posts || []).length)) {
  data.clicks = { days: 7, by_channel: ins.by_channel || [],
    top_posts: (ins.top_posts || []).slice(0, 5).map(p => ({ title: p.title, channel: p.channel, clicks: p.clicks })) };
}
const hk = body('Winning hooks');
if (hk && Array.isArray(hk.styles)) {
  const used = hk.styles.filter(s => s.posts > 0)
    .map(s => ({ hook_style: s.hook_style, posts: s.posts, clicks: s.clicks, clicks_per_post: s.clicks_per_post }));
  if (used.length) data.hooks = { days: 90, styles: used };
}
const cards = $('Collect scorecards').first().json.scorecards || [];
if (cards.length) data.campaigns = cards;
const titles = $('One per pillar').all().map(i => i.json.title);
const pillars = $('Pillar health').all().map((i, n) => ({ b: i.json.body, title: titles[n] }))
  .filter(x => x.b && typeof x.b === 'object' && x.b.pillar_id)
  .map(({ b, title }) => ({ pillar: title, decided: b.decided, approved: b.approved, edited: b.edited, rejected: b.rejected,
    approved_clean_rate: b.approved_clean_rate, rejected_rate: b.rejected_rate, paused: b.paused, reason: b.reason || null }));
if (pillars.length) data.pillars = pillars;
// Comparisons worked out in code. The 7B model combined real numbers into wrong statements
// ("sessions decreased by 1,015 to 1,855"); with finished sentences it only has to quote.
const facts = [];
const fmt = n => typeof n === 'number' ? (Number.isInteger(n) ? n.toLocaleString('en-US') : String(Math.round(n * 1000) / 1000)) : String(n);
if (k && k.totals) {
  const prev = (k.previous || {}).totals || {};
  for (const [m, v] of Object.entries(k.totals)) {
    if (typeof v !== 'number') continue;
    const d = (k.delta_pct || {})[m];
    facts.push(`${m}: ${fmt(v)} last week` + (typeof prev[m] === 'number' ? `, ${fmt(prev[m])} the week before` : '')
      + (typeof d === 'number' ? ` (${d > 0 ? '+' : ''}${fmt(d)}%)` : ''));
  }
}
for (const c of (data.clicks || {}).by_channel || []) facts.push(`${c.channel}: ${fmt(c.clicks)} tracked clicks from ${fmt(c.posts)} posts last week`);
for (const h of (data.hooks || {}).styles || []) facts.push(`hook "${h.hook_style}": ${fmt(h.clicks_per_post)} clicks per post over ${fmt(h.posts)} posts (90 days)`);
// Paid ads (84): finished sentences computed by ads-sync, so the model only quotes them.
const ads = $env.ADS_URL ? body('Ads summary') : null;
if (ads && ads.has_data && Array.isArray(ads.facts)) facts.push(...ads.facts.slice(0, 16));
if (facts.length) data.facts = facts;
return [{ json: { data: JSON.stringify(data), period: `${lw.from} to ${lw.to}`, parts: Object.keys(data) } }];
"""

WEEKLY_CHECK_ACTIONS = r"""
// Numbers are the part a small model invents. Every number in an action's "why" must appear
// in the data it was given (thousands separators and the sign are ignored), or the action is
// dropped; an action without any number is dropped too. At most one action per channel.
// Headline and what-changed lines with a number that is not in the data are removed.
const src = $('Action data').first().json;
const nums = s => (String(s).replace(/(\d),(?=\d{3}\b)/g, '$1').match(/\d+(?:\.\d+)?/g) || []).map(Number);
const known = new Set(nums(src.data + ' ' + src.period));
const supported = s => nums(s).every(x => known.has(x));
const g = $input.first().json;
const o = g && g.output && typeof g.output === 'object' ? g.output : null;
if (!o) {
  const why = (g && g.error && (g.error.message || g.error)) || 'no output';
  return [{ json: { markdown: '', actions: [], dropped: [], note: `next actions skipped: ${String(why).slice(0, 200)}` } }];
}
const dropped = [], seen = new Set(), actions = [];
for (const a of Array.isArray(o.actions) ? o.actions : []) {
  const ch = String(a.channel || '').trim(), key = ch.toLowerCase();
  const reason = !ch || !a.action ? 'empty'
    : !nums(a.why).length ? 'why cites no number'
    : !supported(a.why) ? `number not in the data (${nums(a.why).filter(x => !known.has(x)).join(', ')})`
    : seen.has(key) ? 'second action for the channel' : '';
  if (reason) { dropped.push({ ...a, reason }); continue; }
  seen.add(key);
  actions.push({ channel: ch, action: String(a.action).slice(0, 140), why: String(a.why) });
}
const changed = (Array.isArray(o.what_changed) ? o.what_changed : []).filter(supported).slice(0, 4);
const headline = o.headline && supported(o.headline) ? o.headline : '';
// Blocks separated by a blank line, so the two lists render as two lists.
const md = ['### Next actions',
  headline ? `**${headline}**` : '',
  changed.length ? ['What changed:', '', ...changed.map(c => `- ${c}`)].join('\n') : '',
  ['One next action per channel:', '',
   ...(actions.length ? actions.map(a => `- **${a.channel}**: ${a.action.replace(/\.$/, '')}. _Why:_ ${a.why}`)
                      : ['- None this week: no suggestion was backed by a number in the data.'])].join('\n'),
  dropped.length ? `_${dropped.length} suggestion${dropped.length === 1 ? ' was' : 's were'} dropped: ${dropped.map(d => `${d.channel || '?'} (${d.reason})`).join('; ')}._` : '',
].filter(Boolean).join('\n\n');
return [{ json: { markdown: md, headline, what_changed: changed, actions, dropped, parts: src.parts } }];
"""

# --------------------------------------------------------------------------- 66 weekly newsletter

NEWS_PREPARE = r"""
// The week's content: items published in the last 7 days plus approved blog posts (those
// sit in the CMS as drafts). One issue per ISO week: a calendar item with channel
// "newsletter" and the week in its title means this week is done.
const raw = [].concat($input.first().json.body ?? []);   // full response: the list is in body
const all = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(i => i && i.id);
const isoWeek = d => {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const y = t.getUTCFullYear();
  return `${y}-W${String(Math.ceil(((t - Date.UTC(y, 0, 1)) / 864e5 + 1) / 7)).padStart(2, '0')}`;
};
const now = new Date(), week = isoWeek(now);
const ch = i => String(i.channel).toLowerCase();
const dup = all.find(i => ch(i) === 'newsletter' && String(i.title).includes(week));
if (dup) return [{ json: { go: false, week, text: `*Newsletter ${week}*: skipped, the calendar already has it (#${dup.id}, ${dup.status}).` } }];
const SKIP = ['newsletter', 'review_reply', 'email'];
const since = now.getTime() - 7 * 864e5;
const pub = all.filter(i => i.status === 'published' && Date.parse(i.published_at || '') >= since)
  .sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at));
const blog = all.filter(i => i.status === 'approved' && ch(i) === 'blog');
const seen = new Set();
// Blog posts first (the long pieces), then the newest social posts; at most 8 for a small model.
const picked = [...pub.filter(i => ch(i) === 'blog'), ...blog, ...pub].filter(i => !SKIP.includes(ch(i)) && !seen.has(i.id) && seen.add(i.id)).slice(0, 8);
if (picked.length < 2) return [{ json: { go: false, week,
  text: `*Newsletter ${week}*: skipped, only ${picked.length} item${picked.length === 1 ? '' : 's'} published this week (needs 2).` } }];
const urlsIn = s => (String(s).match(/https?:\/\/\S+/g) || []).map(u => u.replace(/[).,!?]+$/, ''));
const items = picked.map(i => {
  const u = urlsIn(i.body);
  // Blog: the live post (external_url) once published; a CMS draft URL is not public.
  const link = ch(i) === 'blog'
    ? (i.status === 'published' ? i.external_url || i.link : i.link) || u[0] || ''
    : i.short_url || i.link || i.external_url || u[0] || '';
  const text = String(i.body).replace(/https?:\/\/\S+/g, '').replace(/\s+/g, ' ').trim().slice(0, 500);
  return { id: i.id, title: i.title, channel: ch(i), link, text };
});
const items_text = items.map((i, n) => `${n + 1}. [${i.channel}] ${i.title}\n   link: ${i.link || '(none)'}\n   text: ${i.text}`).join('\n');
const links = [...new Set(items.map(i => i.link).filter(Boolean))];
return [{ json: { go: true, week, items, items_text, links } }];
"""

NEWS_BUILD = r"""
// Links come only from the items: any other URL the model wrote is removed and noted.
const p = $('This week').first().json;
const o = $input.first().json.output;
const allowed = new Set(p.links), warnings = [];
const clean = s => String(s || '').replace(/https?:\/\/[^\s)\]]+/g, u => {
  if (allowed.has(u.replace(/[.,!?]+$/, ''))) return u;
  warnings.push(`removed a link not in the items: ${u}`); return '';
}).replace(/[ \t]{2,}/g, ' ').trim();
const sections = (o.sections || []).map(s => {
  let link = String(s.link || '').trim();
  if (link && !allowed.has(link)) { warnings.push(`removed a section link not in the items: ${link}`); link = ''; }
  return { title: clean(s.title), summary: clean(s.summary), link };
}).filter(s => s.title && s.summary);
let cta = String(o.cta_url || '').trim();
if (cta && !allowed.has(cta)) { warnings.push(`removed a CTA link not in the items: ${cta}`); cta = ''; }
if (!cta) cta = sections.map(s => s.link).find(Boolean) || '';
const markdown = [clean(o.intro), ...sections.map(s => `## ${s.title}\n\n${s.summary}${s.link ? `\n\n[Read more](${s.link})` : ''}`)].join('\n\n');
return [{ json: { subject: o.subject, preheader: o.preheader, cta_text: o.cta_text, cta_url: /^https?:\/\//.test(cta) ? cta : '',
  markdown, sections: sections.length, warnings } }];
"""

NEWS_ITEM = r"""
// Calendar item for the issue (channel newsletter, never sent by the publisher 39): in review
// when the quality gate passed, else draft. Notes say where the Listmonk draft is.
const p = $('This week').first().json, b = $('Build issue').first().json, gate = $('Quality gate').first().json;
const r = $input.first().json;
const res = r.body && typeof r.body === 'object' ? r.body : {};
const okHttp = r.statusCode >= 200 && r.statusCode < 300;
let note, url = null;
if (!$env.NEWSLETTER_URL) note = 'NEWSLETTER_URL not set: copy this issue into your email tool by hand';
else if (okHttp && res.status === 'dry_run') note = 'Listmonk dry run (listmonk-bridge DRY_RUN=true): no campaign created';
else if (okHttp) { url = res.url || null; note = `Listmonk draft campaign: ${url || '#' + res.campaign_id}`; }
else note = `Listmonk error ${r.statusCode || ''}: ${JSON.stringify(res.detail ?? res.message ?? r.error ?? r.body ?? '').slice(0, 300)}`;
const notes = [note, `from items ${p.items.map(i => '#' + i.id).join(', ')}`, ...b.warnings,
  ...(gate.problems || []).map(x => `needs a human: ${x}`)].join('\n');
return [{ json: { listmonk_note: note, listmonk_url: url, item: {
  title: `Newsletter ${p.week}: ${b.subject}`.slice(0, 200), channel: 'newsletter',
  body: `Subject: ${b.subject}\nPreheader: ${b.preheader}\n\n${gate.text}`,
  status: gate.ok ? 'in_review' : 'draft', link: b.cta_url || null, notes } } }];
"""


def wf66():
    wf = Workflow(66, "Schedule · Weekly newsletter")
    t = schedule(wf, "Fridays 10:00", "0 10 * * 5")
    cal = http(wf, "Calendar items", "GET", "={{ $env.CALENDAR_URL }}/items", key=True, full_response=True)
    prep = code(wf, "This week", NEWS_PREPARE)
    go = if_true(wf, "Enough items?", "={{ $json.go }}")
    g = gateway(wf, "Write issue", "newsletter_issue",
                "{ week: $json.week, items: $json.items_text, audience: $env.NEWSLETTER_AUDIENCE || null }")
    bld = code(wf, "Build issue", NEWS_BUILD)
    q = call_workflow(wf, "Quality gate", 35, {
        # rewrite "no": the gate's rewrite flattens headings and links into one paragraph
        # (seen in e2e); problems go to the reviewer instead (status draft + notes).
        "text": "={{ $json.markdown }}", "channel": "email", "ref": "={{ $('This week').first().json.week }}",
        "rewrite": "no",
        "context": "={{ $('This week').first().json.items_text }}",
        "links": "={{ $('This week').first().json.links.join(' ') }}"})
    rn = http(wf, "Render HTML", "POST", "={{ $env.EMAIL_RENDER_URL }}/render",
              "={{ JSON.stringify({ subject: $('Build issue').first().json.subject, preheader: $('Build issue').first().json.preheader, body_markdown: $('Quality gate').first().json.text, cta_text: $('Build issue').first().json.cta_url ? $('Build issue').first().json.cta_text : null, cta_url: $('Build issue').first().json.cta_url || null }) }}")
    lm = wf.add("Listmonk draft", "n8n-nodes-base.httpRequest", 4.2, {
        # NEWSLETTER_URL empty: GET the calendar's /health instead; the next node notes it.
        "method": "={{ $env.NEWSLETTER_URL ? 'POST' : 'GET' }}",
        "url": "={{ $env.NEWSLETTER_URL ? $env.NEWSLETTER_URL.replace(/\\/+$/, '') + '/campaigns' : $env.CALENDAR_URL + '/health' }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "X-API-Key", "value": "={{ $env.INTERNAL_API_KEY }}"}]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ name: 'Newsletter ' + $('This week').first().json.week, subject: $('Build issue').first().json.subject, preheader: $('Build issue').first().json.preheader, body_html: $json.html, body_text: $json.text }) }}",
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}}, "timeout": 60000},
    }, onError="continueRegularOutput")
    it = code(wf, "Calendar item", NEWS_ITEM)
    sv = http(wf, "Save to calendar", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json.item) }}", key=True)
    sm = code(wf, "Summary", r"""
const p = $('This week').first().json, c = $('Calendar item').first().json, s = $input.first().json;
return [{ json: { text: `*Newsletter ${p.week}*: ${p.items.length} items → calendar #${s.id} (${s.status}), "${$('Build issue').first().json.subject}". ${c.listmonk_note}.`,
  item_id: s.id, status: s.status, listmonk: c.listmonk_note, listmonk_response: $('Listmonk draft').first().json.body ?? null } }];
""")
    gn = code(wf, "Webhook set?", GATE_NOTIFY, pos=[wf._x, 480])
    n = notify(wf, "Notify", "$json.text", pos=[wf._x + 240, 480], waits="!!$json.item_id")
    wf.chain(t, cal, prep, go)
    wf.link(go, g, src_index=0)
    wf.link(go, gn, src_index=1)   # already done this week, or too few items: say why, no error
    wf.chain(g, bld, q, rn, lm, it, sv, sm, gn, n)
    return wf, {
        "summary": "Every Friday it builds this week's newsletter from the content you already published (JTBD #7): calendar items published in the last 7 days plus approved blog posts (up to 8; review replies, emails and newsletters are left out). The LLM (prompt `newsletter_issue`) writes a subject, preheader, intro and one short section per item; every link must be copied from the items, and the workflow removes any other URL (listed in the notes). The text goes through the quality gate (35) in report-only mode (a rewrite would flatten the headings and links; its problems go into the notes and the item stays `draft`), is rendered as email HTML (18) and sent to the Listmonk bridge (63, `NEWSLETTER_URL/campaigns`) as a **draft** campaign named `Newsletter <ISO week>`: a person reviews and sends it in Listmonk. It also saves a calendar item (channel `newsletter`, `in_review` when the gate passed, else `draft`) whose notes say where the Listmonk draft is; the publisher (39) never sends it. One issue per ISO week: if the calendar already has a `newsletter` item with the week in its title, it stops. With fewer than 2 items it stops with a message. With `NEWSLETTER_URL` empty it still saves the calendar item, to copy by hand.",
        "schedule": "Fridays 10:00",
        "env": {"NEWSLETTER_URL": "listmonk-bridge (63), e.g. `http://listmonk-bridge:8000`; called with `X-API-Key: INTERNAL_API_KEY`. Empty = only the calendar item",
                "NEWSLETTER_AUDIENCE": "optional, e.g. `existing customers`",
                "NOTIFY_WEBHOOK_URL": "optional; receives a one-line summary"},
        "depends": ["19-content-calendar", "03-llm-gateway", "35-wf-tool-quality-gate", "18-email-renderer",
                    "63-listmonk-bridge (optional)"],
    }


ALL_P5 = [wf66]
