"""Phase 3 workflows (57, 59, 60): more content formats, recycling winning posts, review replies."""
from n8nlib import GATE_NOTIFY, Workflow, call_workflow, code, gateway, http, if_true, loop_one_by_one, notify, schedule, sub_trigger
from workflows import VIDEO_REQUEST, VIDEO_RESULT, brand_profile, video_render

# --------------------------------------------------------------------------- 57 content formats

FORMATS_PREPARE = r"""
const s = $input.first().json;
const FORMATS = ['video_script', 'landing_page', 'email_sequence'];
// The chat model paraphrases the format; map what it means, refuse what we can't write.
const ALIAS = {
  video: 'video_script', script: 'video_script', reel: 'video_script', reels: 'video_script',
  tiktok: 'video_script', short: 'video_script', shorts: 'video_script', 'video script': 'video_script',
  landing: 'landing_page', 'landing page': 'landing_page', lp: 'landing_page', page: 'landing_page',
  sequence: 'email_sequence', nurture: 'email_sequence', drip: 'email_sequence',
  'email sequence': 'email_sequence', 'nurture sequence': 'email_sequence', 'drip sequence': 'email_sequence',
};
const raw = String(s.format || '').trim().toLowerCase().replace(/[-\s]+/g, ' ');
const format = FORMATS.includes(raw.replace(/ /g, '_')) ? raw.replace(/ /g, '_') : ALIAS[raw] || null;
const topic = String(s.topic || '').trim();
if (!format) return [{ json: { ok: false, result:
  `Unknown format "${s.format || ''}". write_content_format writes one of: video_script, landing_page, email_sequence. ` +
  'For social posts use write_social_posts, for a single newsletter write_email, for an article write_blog_post.' } }];
if (!topic) return [{ json: { ok: false, result:
  `What should the ${format.replace('_', ' ')} be about? Give a topic, product or goal.` } }];
return [{ json: { ok: true, format, topic, audience: String(s.audience || '').trim(),
  offer: String(s.offer || '').trim(), details: String(s.details || '').trim() } }];
"""

FORMATS_VARS = r"""
const p = $('Prepare').first().json;
const kb = ($('Search knowledge base').first().json.results || []).filter(r => r.score >= 0.35)
  .map((r, i) => `[${i + 1}] (${r.title}) ${r.chunk}`).join('\n');
// Details the user typed count as facts for the writer and as evidence for the claim checker.
const context = [p.details, kb].filter(Boolean).join('\n') || null;
const common = { audience: p.audience || null, context };
// Real, consented customer quotes from the proof bank (58), up to 3, for the landing page's
// social proof. The model may only copy one exactly; the gate checks every quotation.
const res = $input.first().json;   // full response; an error or /health when not a landing page
const bank = Array.isArray(res.body) ? res.body : [];
const quotes = bank.filter(t => t && t.consent === true && t.quote && t.author_display)
  .map(t => `"${t.quote}" — ${t.author_display}`).filter(l => l.length <= 240).slice(0, 3);
const testimonials = quotes.length ? quotes.map(q => `- ${q}`).join('\n') : null;
const vars = {
  video_script: { topic: p.topic + (p.offer ? ` (offer: ${p.offer})` : ''), ...common },
  landing_page: { product: p.topic, offer: p.offer || null, testimonials, ...common },
  email_sequence: { goal: p.topic, offer: p.offer || null, ...common },
}[p.format];
return [{ json: { prompt: p.format, vars, context, testimonials: p.format === 'landing_page' ? quotes : [] } }];
"""

# Only the copy goes through the quality gate. Labels carry no digits and are not ALL CAPS:
# the claim checker treats every number it did not get as evidence as invented, and the
# brand check flags shouting. Shot notes, lengths and day offsets are added after the gate.
FORMATS_FOR_GATE = r"""
const p = $('Prepare').first().json;
const out = $input.first().json.output;
const bv = $('Build vars').first().json;
// The testimonials are real customer words: evidence for the claim checker, like the context.
const gateCtx = [p.topic, p.offer, bv.context, ...(bv.testimonials || [])].filter(Boolean).join('\n');
const tag = h => '#' + String(h).replace(/^#/, '').replace(/\s+/g, '');
const item = (ref, channel, text) => ({ json: { ref: String(ref), channel, text, context: gateCtx } });
if (p.format === 'video_script') {
  const lines = [`Hook: ${out.hook.spoken}`, `On screen: ${out.hook.on_screen}`, ''];
  for (const b of out.beats) lines.push(`Say: ${b.spoken}`, `On screen: ${b.on_screen}`, '');
  lines.push(`Close: ${out.cta}`, '', `Caption: ${out.caption}`, out.hashtags.map(tag).join(' '));
  return [item(0, 'video', lines.join('\n'))];
}
if (p.format === 'landing_page') {
  const lines = [`# ${out.headline}`, out.subheadline, ''];
  for (const b of out.benefits) lines.push(`## ${b.title}`, b.text, '');
  // The model paraphrases the placeholder ("[Add a customer quote if needed]"): one spelling.
  const sp = /^\s*\[/.test(out.social_proof) ? '[add a customer quote]' : out.social_proof;
  lines.push(`Social proof: ${sp}`, '', '## Questions');
  for (const f of out.faq) lines.push(`Q: ${f.question}`, `A: ${f.answer}`, '');
  lines.push(`Button: ${out.cta_text}`);
  return [item(0, 'landing_page', lines.join('\n'))];
}
return out.emails.map((e, i) => item(i, 'email',
  [`Subject: ${e.subject}`, `Preview: ${e.preview}`, '', e.body, '', `Button: ${e.cta_text}`].join('\n')));
"""

FORMATS_ASSEMBLE = r"""
const p = $('Prepare').first().json;
const out = $('Write').first().json.output;
const gate = $input.all().map(i => i.json);
// The gate returns items out of order (fixed ones first): match them by ref.
const byRef = Object.fromEntries(gate.map(g => [String(g.ref), g]));
const refs = Object.keys(byRef).sort((a, b) => a - b);
const problems = [], fixed = [], warnings = [];
for (const r of refs) {
  const g = byRef[r];
  const where = p.format === 'email_sequence' ? `email ${Number(r) + 1}: ` : '';
  problems.push(...(g.problems || []).map(x => where + x));
  if (g.rewritten) fixed.push(...(g.fixed || []).map(x => where + x));
  warnings.push(...(g.warnings || []).map(x => where + x));
}
let title, body, channel;
if (p.format === 'video_script') {
  channel = 'video';
  title = `Video script: ${p.topic}`;
  body = [byRef['0'].text, '', `Estimated length: ${out.estimated_seconds} s`, 'Shot list:',
    `- hook: (film what the hook says)`, ...out.beats.map((b, i) => `- beat ${i + 1}: ${b.shot}`)].join('\n');
} else if (p.format === 'landing_page') {
  channel = 'landing_page';
  title = `Landing page: ${out.headline}`;
  body = byRef['0'].text;
  const sp = /^\s*\[/.test(out.social_proof) ? '[add a customer quote]' : String(out.social_proof);
  // With no testimonials given, a quote in quotation marks means the model wrote one nobody
  // gave us. With testimonials, the gate (35) checked every quotation against the proof bank.
  const given = ($('Build vars').first().json.testimonials || []).length > 0;
  if (!given && !sp.includes('[add a customer quote]') && /["“”]/.test(sp))
    problems.push('social proof looks like a customer quote: use a real one or "[add a customer quote]"');
} else {
  channel = 'email_sequence';
  title = `Email sequence: ${p.topic}`;
  body = out.emails.map((e, i) => `--- Email ${i + 1} of ${out.emails.length} · day ${e.day} ---\n${byRef[String(i)].text}`).join('\n\n');
}
const notes = [];
if (fixed.length) notes.push('Auto-fixed: ' + fixed.join('; '));
if (problems.length) notes.push('Quality gate: ' + problems.join('; '));
return [{ json: { title: title.slice(0, 120), channel, body, status: problems.length ? 'draft' : 'in_review',
  notes: notes.join(' | ') || null, fixed, problems, warnings } }];
"""

FORMATS_REPLY = r"""
const a = $('Assemble').first().json;
const v = $('Attach video').first().json;
const s = $input.first().json;
const label = { video: 'Video script', landing_page: 'Landing page', email_sequence: 'Email sequence' }[a.channel];
const notes = [];
if (a.fixed.length) notes.push(`auto-fixed: ${a.fixed.join('; ')}`);
if (a.problems.length) notes.push(`needs a human: ${a.problems.join('; ')}`);
if (a.warnings.length) notes.push(`warnings: ${[...new Set(a.warnings)].join('; ')}`);
if (v.video_note) notes.push(v.video_note);
return [{ json: { result: [
  `${label} saved to the content calendar as #${s.id} (${s.status}).`,
  notes.length ? `_${notes.join(' · ')}_` : '', '', a.body, '',
  s.status === 'in_review' ? 'It is waiting in the approval form; nothing is published until approved.'
    : 'Saved as a draft because the quality gate found problems a person should fix.',
].filter((x, i) => i !== 1 || x).join('\n') } }];
"""


def wf57():
    wf = Workflow(57, "Tool · Content formats")
    s = sub_trigger(wf, [(k, "string") for k in ("format", "topic", "audience", "offer", "details")])
    p = code(wf, "Prepare", FORMATS_PREPARE)
    ok = if_true(wf, "Known format?", "={{ $json.ok }}")
    bad = code(wf, "Explain", "return [{ json: { result: $input.first().json.result } }];", pos=[wf._x, 520])
    kb = http(wf, "Search knowledge base", "POST", "={{ $env.KB_URL }}/search",
              "={{ JSON.stringify({ query: $('Prepare').first().json.topic, k: 4 }) }}", continue_on_fail=True)
    # Fail-soft: without REVIEWS_URL, or for other formats, it asks the knowledge base's
    # /health instead and Build vars finds no testimonials.
    tm = http(wf, "Testimonials", "GET",
              "={{ $env.REVIEWS_URL && $('Prepare').first().json.format === 'landing_page' ? "
              "$env.REVIEWS_URL + '/testimonials?usable=true' : $env.KB_URL + '/health' }}",
              full_response=True, never_error=True, continue_on_fail=True, timeout=30000)
    bv = code(wf, "Build vars", FORMATS_VARS)
    # The prompt name is chosen by the format, so this is gateway() with a dynamic prompt.
    w = http(wf, "Write", "POST", "={{ $env.GATEWAY_URL }}/v1/run",
             "={{ JSON.stringify({ prompt: $json.prompt, vars: $json.vars }) }}", key=True, llm=True)
    fg = code(wf, "For the gate", FORMATS_FOR_GATE)
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.text }}", "channel": "={{ $json.channel }}",
                                               "context": "={{ $json.context }}", "ref": "={{ $json.ref }}",
                                               "rewrite": "no"})
    asm = code(wf, "Assemble", FORMATS_ASSEMBLE)
    # A video script gets a preview MP4 from 71 (VIDEO_URL) before it is saved; fail-soft.
    bp = brand_profile(wf)
    vq = code(wf, "Video request", VIDEO_REQUEST + r"""
const p = $('Prepare').first().json;
return [{ json: videoRequest(p.format === 'video_script' ? $('Write').first().json.output : null) }];
""")
    vr = video_render(wf)
    av = code(wf, "Attach video", VIDEO_RESULT + r"""
const a = $('Assemble').first().json;
const v = videoResult($('Video request').first().json, $input.first().json);
return [{ json: { ...a, ...v, image_url: v.poster_url,
  notes: [a.notes, v.video_note].filter(Boolean).join(' | ') || null } }];
""")
    sv = http(wf, "Save draft", "POST", "={{ $env.CALENDAR_URL }}/items",
              "={{ JSON.stringify({ title: $json.title, channel: $json.channel, body: $json.body, "
              "status: $json.status, notes: $json.notes, video_url: $json.video_url, image_url: $json.image_url }) }}",
              key=True)
    rp = code(wf, "Reply", FORMATS_REPLY)
    wf.chain(s, p, ok)
    wf.link(ok, kb, src_index=0)
    wf.link(ok, bad, src_index=1)
    wf.chain(kb, tm, bv, w, fg, q, asm, bp, vq, vr, av, sv, rp)
    return wf, {
        "summary": "Writes three formats the other writers don't: a short-form vertical **video script** (Reels/TikTok/Shorts, 30–60 s: hook, beats with spoken line, on-screen text and shot, CTA, caption, hashtags), **landing page** copy (hero, benefit blocks, social proof, answer-first FAQ, CTA) and a 3–5 email **nurture sequence** (day, subject, preview, body, CTA per email). It pulls context from the knowledge base (06), writes through the gateway (03) with the approved facts, runs the copy through the quality gate (35) and saves one draft to the calendar (19) for approval. For a landing page it first asks the review hub (58, `REVIEWS_URL`) for up to 3 testimonials with consent; the social proof must be one of them copied exactly, with its author, or `[add a customer quote]`, and the gate checks every quotation against the proof bank. Without the review hub it uses an approved fact or the placeholder. A video script is also rendered as a preview MP4 by 71 (`POST $VIDEO_URL/render`: the script, the brand name from 05, the voice from `VIDEO_VOICE` or 71's default); the item gets it as `video_url`, the poster as `image_url` and the note `video preview: <url> (<n> s)`. A render that fails (422 too long, 429 busy, service down) only adds `video not rendered: <reason>`; the draft is saved either way. Without `VIDEO_URL` no video is made.",
        "inputs": {"format": "`video_script`, `landing_page` or `email_sequence` (also accepts e.g. `reel`, `landing page`, `nurture`)",
                   "topic": "what the video is about / the product the page sells / the goal of the sequence",
                   "audience": "optional", "offer": "optional", "details": "optional facts the user gave for this piece"},
        "output": "`{result}`: calendar id and status, quality notes and the full piece. An unknown format returns a message listing the three",
        "depends": ["03-llm-gateway", "06-knowledge-base", "19-content-calendar", "35-wf-tool-quality-gate",
                    "58-review-hub (optional, landing page testimonials)",
                    "05-brand-service (optional, brand name on the video)", "71-video-assembly (optional, `VIDEO_URL`)"],
        "env": {"VIDEO_URL": "the video service (71), e.g. `http://video-assembly:8000`; empty = video scripts get no preview video",
                "VIDEO_VOICE": "voice for the preview: `piper`, `say` (macOS only) or `none`; empty = 71's `TTS_BACKEND`"},
        "called_by": "the chat agent (24), tool `write_content_format`",
        "test": {"format": "video_script", "topic": "How our decaf is made", "audience": "afternoon coffee drinkers",
                 "offer": "", "details": ""},
    }


# --------------------------------------------------------------------------- 59 winner recycler

RECYCLE_PICK = r"""
// Winners = published posts whose tracked short links earned the most clicks (45 insights).
const res = $('Top posts').first().json;   // full response: the insights object is in body
const ins = (res.body && typeof res.body === 'object') ? res.body : {};
const top = Array.isArray(ins.top_posts) ? ins.top_posts : [];
const raw = [].concat($input.first().json.body ?? []);
const items = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(i => i && i.id);
const byId = new Map(items.map(i => [Number(i.id), i]));

const num = (v, d) => { const n = Number(v); return v === undefined || v === null || v === '' || !Number.isFinite(n) || n < 0 ? d : n; };
const MIN_CLICKS = num($env.RECYCLE_MIN_CLICKS, 5);
const MIN_AGE_DAYS = num($env.RECYCLE_MIN_AGE_DAYS, 30);
const PER_WEEK = Math.floor(num($env.RECYCLE_PER_WEEK, 2));
const DAY = 864e5, now = Date.now();
const SOCIAL = ['x', 'linkedin', 'instagram', 'facebook', 'threads', 'mastodon'];  // what 30 can write

// Drafts made by this workflow carry "recycled from #<id>" in their notes (30 writes it).
const recycled90 = new Set(), recycled7 = new Set();
for (const i of items) {
  const age = now - Date.parse(i.created_at || '');
  for (const m of String(i.notes || '').matchAll(/recycled from #(\d+)\b/g)) {
    if (age <= 90 * DAY) recycled90.add(Number(m[1]));
    if (age <= 7 * DAY) recycled7.add(Number(m[1]));
  }
}
// The cap counts posts recycled in the last 7 days, so firing it twice in a week can't double it.
const room = Math.max(0, PER_WEEK - recycled7.size);

const skipped = [], winners = [];
const sorted = [...top].sort((a, b) => (Number(b.clicks) || 0) - (Number(a.clicks) || 0) || a.item_id - b.item_id);
for (const t of sorted) {
  const id = Number(t.item_id), clicks = Number(t.clicks) || 0, item = byId.get(id);
  const why =
    clicks < MIN_CLICKS ? null :   // below the bar: not worth listing
    !item ? 'not in the calendar' :
    item.status !== 'published' ? `status is ${item.status}` :
    !item.published_at || now - Date.parse(item.published_at) < MIN_AGE_DAYS * DAY ? `published less than ${MIN_AGE_DAYS} days ago` :
    recycled90.has(id) ? 'already recycled in the last 90 days' :
    !SOCIAL.includes(String(item.channel).toLowerCase()) ? `channel ${item.channel} is not a social channel` : '';
  if (why === null) continue;
  if (why) { skipped.push(`#${id} (${clicks} clicks): ${why}`); continue; }
  // The post keeps its words, not its old tracked link: a URL carrying utm_content=<old id>
  // would credit the new post's clicks to the old one. Pass the clean target as `link`.
  const stripUtm = u => {
    const [base, q = ''] = String(u).split('?');
    const kept = q.split('&').filter(p => p && !/^utm_/i.test(p));
    return kept.length ? `${base}?${kept.join('&')}` : base;
  };
  const shortBase = item.short_url ? String(item.short_url).replace(/\/[^/]*$/, '/') : null;
  const urls = (String(item.body).match(/https?:\/\/\S+/g) || []).map(u => u.replace(/[).,!?]+$/, ''));
  const target = item.link || urls.find(u => !shortBase || !u.startsWith(shortBase)) || '';
  const text = String(item.body).replace(/https?:\/\/\S+/g, '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  if (!text) { skipped.push(`#${id} (${clicks} clicks): the post has no text besides its link`); continue; }
  if (winners.length >= room) { skipped.push(`#${id} (${clicks} clicks): over the weekly cap of ${PER_WEEK}`); continue; }
  winners.push({ winner: true, item_id: id, title: item.title, channel: String(item.channel).toLowerCase(), clicks,
    text, link: target ? stripUtm(target) : '', note: `recycled from #${id} (${clicks} clicks)` });
}
if (winners.length) return winners.map(w => ({ json: { ...w, skipped } }));
const errs = (ins.errors || []).map(e => e.source + ': ' + (e.error || '')).join('; ');
return [{ json: { winner: false, text: [
  `*Winner recycler*: nothing to recycle this week (posts with at least ${MIN_CLICKS} clicks, published ${MIN_AGE_DAYS}+ days ago, not recycled in 90 days; cap ${PER_WEEK} a week, ${recycled7.size} used).`,
  ...skipped.map(x => `- ${x}`), errs ? `⚠ insights had errors: ${errs}` : '',
].filter(Boolean).join('\n') } }];
"""

RECYCLE_SUMMARY = r"""
const winners = $('Pick winners').all().map(i => i.json);
const lines = $input.all().map((r, i) => {
  const w = winners[i] || {};
  const saved = [...String(r.json.result || '').matchAll(/calendar #(\d+) \((\w+)\)/g)].map(m => `#${m[1]} (${m[2]})`);
  return `- #${w.item_id} ${w.channel}, ${w.clicks} clicks, "${w.title}" → ${saved.join(', ') || 'no draft saved'}`;
});
const skipped = (winners[0] || {}).skipped || [];
return [{ json: { text: [
  `*Winner recycler*: ${winners.length} top post${winners.length === 1 ? '' : 's'} rewritten as ${winners.length === 1 ? 'a new draft' : 'new drafts'}. Review in the approval form.`,
  ...lines, ...(skipped.length ? ['Skipped:', ...skipped.map(x => `- ${x}`)] : []),
].join('\n') } }];
"""


def wf59():
    wf = Workflow(59, "Schedule · Winner recycler")
    t = schedule(wf, "Mondays 08:00", "0 8 * * 1")
    ins = http(wf, "Top posts", "GET", "={{ $env.CAMPAIGNS_URL }}/insights", query={"days": "90"},
               full_response=True, timeout=60000)
    cal = http(wf, "Calendar items", "GET", "={{ $env.CALENDAR_URL }}/items", key=True, full_response=True)
    pick = code(wf, "Pick winners", RECYCLE_PICK)
    any_ = if_true(wf, "Any winners?", "={{ $json.winner }}")
    # One run of 30 per winner: it rewrites the post (new hook, same facts), runs the quality
    # gate and saves the draft with the note, so the next run sees it was recycled.
    rc = call_workflow(wf, "Recycle", 30, {"url": "", "text": "={{ $json.text }}",
                                           "channels": "={{ $json.channel }}", "link": "={{ $json.link }}",
                                           "note": "={{ $json.note }}"}, mode="each")
    sm = code(wf, "Summary", RECYCLE_SUMMARY)
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text", waits="$json.winner !== false")
    wf.chain(t, ins, cal, pick, any_)
    wf.link(any_, rc, src_index=0)
    wf.link(any_, gn, src_index=1)   # nothing qualifies: report why, no drafts, no error
    wf.chain(rc, sm, gn, n)
    return wf, {
        "summary": "Every Monday it takes the posts whose tracked short links earned the most clicks in the last 90 days (45 insights, from 16 clicks) and turns the best ones into fresh drafts: same channel, same facts, a new opening, through the repurpose tool (30), which runs the quality gate (35) and saves each draft to the calendar (19) with the note `recycled from #<id> (<n> clicks)`. A post qualifies when it has at least `RECYCLE_MIN_CLICKS` clicks, was published at least `RECYCLE_MIN_AGE_DAYS` days ago and was not recycled in the last 90 days. At most `RECYCLE_PER_WEEK` posts are recycled per 7 days, most clicks first (fewer, better). The old tracked link is not copied: the draft gets the clean target URL, so the publisher (39) tracks the new post under its own id. Nothing is published until a person approves the draft. When nothing qualifies it posts the reasons and stops.",
        "schedule": "Mondays 08:00",
        "env": {"RECYCLE_MIN_CLICKS": "clicks a post needs to be recycled; default 5",
                "RECYCLE_MIN_AGE_DAYS": "days since publishing before a post can be recycled; default 30",
                "RECYCLE_PER_WEEK": "most posts recycled per 7 days; default 2 (0 turns recycling off)",
                "NOTIFY_WEBHOOK_URL": "optional; receives the weekly summary"},
        "depends": ["45-campaign-service", "16-link-shortener", "19-content-calendar", "30-wf-tool-repurpose",
                    "35-wf-tool-quality-gate"],
    }




# --------------------------------------------------------------------------- 60 review replies

REVIEWS_PICK = r"""
// New reviews from the review hub (58); full response, so an empty list is still one item.
const raw = [].concat($('New reviews').first().json.body ?? []);
const reviews = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(r => r && r.id);
const cal = [].concat($input.first().json.body ?? []);
const items = (cal.length === 1 && Array.isArray(cal[0]) ? cal[0] : cal).filter(i => i && i.id);
const num = (v, d) => { const n = Number(v); return v === undefined || v === null || v === '' || !Number.isFinite(n) || n < 0 ? d : n; };
const CAP = Math.floor(num($env.REVIEW_REPLIES_PER_RUN, 10));
// A review that already has a reply draft (notes "review #<id> (") is not drafted twice, e.g.
// when a run saved the draft but stopped before marking the review drafted.
const drafted = new Map();
for (const i of items) for (const m of String(i.notes || '').matchAll(/review #(\d+) \(/g)) drafted.set(Number(m[1]), i.id);
const already = reviews.filter(r => drafted.has(r.id)).map(r => `review #${r.id} already has draft #${drafted.get(r.id)}: set the review's status by hand`);
// Oldest first, so a backlog is answered in the order it came in.
const todo = reviews.filter(r => !drafted.has(r.id)).sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || a.id - b.id);
const picked = todo.slice(0, CAP);
const later = todo.length - picked.length;
const notes = [...already, ...(later > 0 ? [`${later} more new review${later === 1 ? '' : 's'} left for the next run (REVIEW_REPLIES_PER_RUN=${CAP})`] : [])];
if (picked.length) return picked.map(r => ({ json: { pick: true, id: r.id, source: r.source, rating: r.rating, notes } }));
return [{ json: { pick: false, text: [`*Review replies*: no new reviews to draft.`, ...notes.map(n => `- ${n}`)].join('\n'), notes } }];
"""

REVIEWS_DECIDE = r"""
const r = $('Reply context').first().json;   // one review per loop round
const out = ($input.first().json || {}).output || {};
const id = $('Loop').first(1).json.id ?? r.review_id;  // (output 1 = the current review)
const errText = j => { const e = j.error ?? j.detail; return e == null ? '' : String(typeof e === 'object' ? e.message || JSON.stringify(e) : e).slice(0, 200); };
if (!r.review_id) return [{ json: { save: false, review_id: id, error: `no reply context (${errText(r) || 'review hub error'})` } }];
const reply = String(out.reply || '').trim();
if (!reply) return [{ json: { save: false, review_id: r.review_id, source: r.source, rating: r.rating,
  error: `no reply from the model (${errText($input.first().json) || 'gateway error'})` } }];
// The code's flag wins: health, safety and legal words go to a person whatever the model says.
const escalate = out.needs_human === true || r.needs_human === true;
const why = (r.escalate_words || []).length ? r.escalate_words.join(', ') : String(out.reason || 'the model asked for a person');
const quoted = String(r.review).split('\n').map(l => `> ${l}`).join('\n');
return [{ json: {
  save: true, gate: !escalate, review_id: r.review_id, source: r.source, rating: r.rating, escalated: escalate,
  title: `Reply to ${r.source} review #${r.review_id} (${r.rating}★)`.slice(0, 120),
  channel: 'review_reply', reply, body: `${reply}\n\n---\n${quoted}`, context: r.review,
  status: 'draft',
  notes: [`review #${r.review_id} (${r.source})`, escalate ? `needs a person: ${why}` : ''].filter(Boolean).join(' | '),
} }];
"""

REVIEWS_AFTER_GATE = r"""
const d = $('Has reply?').first().json;
const g = $input.first().json;
const problems = Array.isArray(g.problems) ? g.problems : ['quality gate gave no result'];
return [{ json: { ...d, status: problems.length ? 'draft' : 'in_review',
  notes: [d.notes, problems.length ? `Quality gate: ${problems.join('; ')}` : ''].filter(Boolean).join(' | ') } }];
"""

REVIEWS_RECORD_SAVED = r"""
const d = $('Save item').first().json;
const r = $('Mark drafted').first().json;
const x = $('Has reply?').first().json;
return [{ json: { review_id: x.review_id, source: x.source, rating: x.rating, item_id: d.id, status: d.status,
  escalated: x.escalated, marked: r.status === 'drafted', notes: d.notes } }];
"""

REVIEWS_SUMMARY = r"""
const rows = $input.all().map(i => i.json);
const saved = rows.filter(r => r.item_id);
const failed = rows.filter(r => !r.item_id);
const notes = ($('Pick reviews').first().json.notes) || [];
const line = r => `- ${r.source} review #${r.review_id} (${r.rating}★) → calendar #${r.item_id} (${r.status})` +
  (r.escalated ? ' · needs a person' : '') + (r.marked ? '' : ' · review NOT marked drafted');
return [{ json: { text: [
  `*Review replies*: ${saved.length} reply draft${saved.length === 1 ? '' : 's'} saved to the calendar. ` +
  'Nothing is posted: after approval, a person copies each reply to Google or Trustpilot.',
  ...saved.map(line),
  ...failed.map(r => `- review #${r.review_id}: not drafted, ${r.error} (it stays new and is retried next run)`),
  ...notes.map(n => `- ${n}`),
].join('\n'), saved: saved.length, failed: failed.length, items: rows } }];
"""


def wf60():
    wf = Workflow(60, "Schedule · Review replies")
    t = schedule(wf, "Daily 09:00", "0 9 * * *")
    hs = code(wf, "Hub set?", "return $env.REVIEWS_URL ? $input.all() : [];   // no review hub: finish, nothing to do")
    nr = http(wf, "New reviews", "GET", "={{ $env.REVIEWS_URL }}/reviews", query={"status": "new"},
              full_response=True, timeout=30000)
    cal = http(wf, "Reply drafts", "GET", "={{ $env.CALENDAR_URL }}/items", query={"channel": "review_reply"},
               key=True, full_response=True)
    pick = code(wf, "Pick reviews", REVIEWS_PICK)
    any_ = if_true(wf, "Any reviews?", "={{ $json.pick }}")
    loop = loop_one_by_one(wf, "Loop")
    ctx = http(wf, "Reply context", "GET", "={{ $env.REVIEWS_URL }}/reviews/{{ $json.id }}/reply-context",
               never_error=True, continue_on_fail=True, timeout=30000)
    dr = gateway(wf, "Draft reply", "review_reply", "$json.vars", continue_on_fail=True)
    dec = code(wf, "Decide", REVIEWS_DECIDE)
    hr = if_true(wf, "Has reply?", "={{ $json.save }}")
    ga = if_true(wf, "Gate it?", "={{ $json.gate }}")
    # Escalated replies skip the gate: they stay drafts for a person anyway.
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.reply }}", "channel": "review_reply",
                                               "context": "={{ $json.context }}", "ref": "={{ $json.review_id }}",
                                               "rewrite": "no"}, pos=[wf._x, 200])
    ag = code(wf, "After gate", REVIEWS_AFTER_GATE, pos=[wf._x, 200])
    sv = http(wf, "Save item", "POST", "={{ $env.CALENDAR_URL }}/items",
              "={{ JSON.stringify({ title: $json.title, channel: $json.channel, body: $json.body, "
              "status: $json.status, notes: $json.notes }) }}", key=True)
    mk = http(wf, "Mark drafted", "POST",
              "={{ $env.REVIEWS_URL }}/reviews/{{ $('Has reply?').first().json.review_id }}/status",
              "={{ JSON.stringify({ status: 'drafted' }) }}", key=True)
    rs = code(wf, "Record saved", REVIEWS_RECORD_SAVED)
    rk = code(wf, "Record skipped", "return [{ json: $input.first().json }];   // stays new: retried next run",
              pos=[wf._x, 520])
    sm = code(wf, "Summary", REVIEWS_SUMMARY, pos=[wf._x, 100])
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text", waits="$json.saved > 0")
    nothing = code(wf, "Nothing new", "return $input.all();   // no notification for a quiet day", pos=[wf._x, 520])
    wf.chain(t, hs, nr, cal, pick, any_)
    wf.link(any_, loop, src_index=0)
    wf.link(any_, nothing, src_index=1)
    wf.link(loop, sm, src_index=0)      # done: every recorded review
    wf.link(loop, ctx, src_index=1)     # one review per round (the local LLM is slow)
    wf.chain(ctx, dr, dec, hr)
    wf.link(hr, ga, src_index=0)
    wf.link(hr, rk, src_index=1)
    wf.link(ga, q, src_index=0)
    wf.link(ga, sv, src_index=1)
    wf.chain(q, ag, sv, mk, rs)
    wf.link(rs, loop)
    wf.link(rk, loop)
    wf.chain(sm, gn, n)
    return wf, {
        "summary": "Every morning it drafts replies to new customer reviews. It takes the reviews with status `new` from the review hub (58), at most `REVIEW_REPLIES_PER_RUN` per run, oldest first, and one at a time: gets the reply context (58), writes a reply with the `review_reply` prompt through the gateway (03, with the approved facts), and saves it to the content calendar (19) as channel `review_reply`, titled `Reply to <source> review #<id> (<rating>★)`. The body is the reply, a line `---`, and the original review quoted. A review that mentions health, safety or legal action (decided in code by 58, or by the model's `needs_human`) is saved as a `draft` with the note `needs a person: <why>`, and a member of the team handles it. Every other reply goes through the quality gate (35, report only): with no problems it goes to the approval form as `in_review`, otherwise it stays a `draft` with the problems in its notes. Then the review is marked `drafted` in 58, so the next run skips it (a review that already has a reply draft in the calendar is never drafted twice).\n\n**Nothing is posted automatically.** Nothing goes to Google, Trustpilot or any other platform, and the publisher (39) skips `review_reply` items even when approved: after approval, a person copies the reply, posts it on the platform, and sets the review to `replied` in 58. If the model or the review hub fails for one review, that review stays `new` and is retried the next day. A summary goes to `NOTIFY_WEBHOOK_URL` when any reply was drafted. With `REVIEWS_URL` empty the workflow does nothing.",
        "schedule": "Daily 09:00",
        "env": {"REVIEWS_URL": "the review hub (58); empty = the workflow does nothing",
                "REVIEW_REPLIES_PER_RUN": "most reviews drafted per run; default 10 (the rest wait for the next day)",
                "NOTIFY_WEBHOOK_URL": "optional; receives the summary"},
        "depends": ["58-review-hub", "03-llm-gateway", "04-prompt-library (prompt `review_reply`)",
                    "19-content-calendar", "35-wf-tool-quality-gate"],
    }


ALL_P3 = [wf57, wf59, wf60]
