"""Definitions of the 20 n8n workflows (deploys 24-43). Run build.py to write them."""
from n8nlib import (FORMS_CRED, GATEWAY_CHAT_CRED, HOSTED_CRED, OLLAMA_CRED, WF_IDS, Workflow, call_workflow, code, gateway, http,
                    if_true, mapper, merge_append, notify, schedule, sub_trigger, GATE_NOTIFY, LLM_TIMEOUT)

# --------------------------------------------------------------------------- shared JS

def quote_check(wf, name, text_js, pos=None):
    """POST the text to 58 /testimonials/check. With REVIEWS_URL empty it GETs the brand
    service's /health instead (the same trick as 33's Current item), and the caller ignores it."""
    return wf.add(name, "n8n-nodes-base.httpRequest", 4.2, {
        "method": "={{ $env.REVIEWS_URL ? 'POST' : 'GET' }}",
        "url": "={{ $env.REVIEWS_URL ? $env.REVIEWS_URL + '/testimonials/check' : $env.BRAND_URL + '/health' }}",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ text: %s }) }}" % text_js,
        "options": {"response": {"response": {"neverError": True}}, "timeout": 30000},
    }, pos=pos, onError="continueRegularOutput")


NORMALIZE_CHANNELS = r"""
const KNOWN = ['x', 'linkedin', 'instagram', 'facebook', 'threads', 'mastodon'];
const ALIAS = { twitter: 'x', 'x/twitter': 'x', ig: 'instagram', insta: 'instagram', fb: 'facebook' };
function normalizeChannels(raw) {
  const list = String(raw || 'linkedin')
    .split(/,|;|\/| and |&/)
    .map(c => c.trim().toLowerCase())
    .map(c => ALIAS[c] || c)
    .filter(c => KNOWN.includes(c));
  const unique = [...new Set(list)];
  if (!unique.length) throw new Error(`No supported channel in "${raw}". Use: ${KNOWN.join(', ')}`);
  return unique;
}
"""

SPLIT_POSTS = r"""
// Small models sometimes add channels nobody asked for, or drop the link. Fix both here,
// deterministically, instead of trusting the model. The model may also answer with an old
// channel name ("twitter" when "x" was asked for), so its names go through the same aliases.
const OUT_ALIAS = { twitter: 'x', 'x/twitter': 'x', 'x (twitter)': 'x', ig: 'instagram', insta: 'instagram', fb: 'facebook' };
const p = $('Prepare').first().json;
const seen = new Set();
const out = [];
for (const post of $input.first().json.output.posts || []) {
  const named = String(post.channel || '').trim().toLowerCase();
  const channel = OUT_ALIAS[named] || named;
  if (!p.channels.includes(channel) || seen.has(channel)) continue;
  seen.add(channel);
  let text = String(post.text || '').trim();
  if (p.link && !text.includes(p.link)) text += `\n\n${p.link}`;
  out.push({ json: { channel, text } });
}
if (!out.length) throw new Error('The model returned no posts for the requested channels');
return out;
"""

APPLY_UTM = r"""
const post = $('Split posts').item.json;
const p = $('Prepare').first().json;
const tagged = $json.url;  // utm-builder answer; absent when there is no link
const text = p.link && tagged ? post.text.split(p.link).join(tagged) : post.text;
return { json: { channel: post.channel, text, link: tagged || p.link || null } };
"""

SAVE_ITEM_BODY = (
    "={{ JSON.stringify({ title: %s, channel: $json.channel, body: $json.text, "
    "status: $json.ok ? 'in_review' : 'draft', campaign: %s, link: %s, "
    "notes: $json.problems.length ? 'Quality gate: ' + $json.problems.join('; ') : null }) }}"
)

# ---------- images: a title card from 17 for every post on an image-friendly channel

def brand_profile(wf, name="Brand profile"):
    """GET the brand profile (05) once: its name goes on the cards. Fail-soft."""
    return http(wf, name, "GET", "={{ $env.BRAND_URL }}/profile", never_error=True, continue_on_fail=True,
                full_response=True, timeout=10000, key=True)


CARD_REQUEST = r"""
// Image-friendly channels get a title card from 17 (17 picks the size from the channel:
// square for instagram/facebook/threads, og for linkedin/x). Other channels, or no
// CARDS_URL, make a harmless GET /health instead so every item still passes through.
const CARD_CHANNELS = ['instagram', 'facebook', 'threads', 'linkedin', 'x'];
function cardRequest(channel, title) {
  const b = ($('Brand profile').first().json || {}).body;
  const brand = b && typeof b === 'object' ? b : {};
  // First line without links, hashtags or emoji (the card font has no emoji glyphs), cut to
  // its first sentence when that is long enough to stand alone.
  const first = String(title || '').split('\n')
    .map(l => l.replace(/https?:\/\/\S+/g, '').replace(/(^|\s)#[\p{L}\p{N}_]+/gu, '$1')
      .replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}\u{20E3}]/gu, '').replace(/\s+/g, ' ').trim())
    .find(l => /[\p{L}\p{N}]/u.test(l)) || '';
  const sentence = (first.match(/^.*?[.!?](?=\s|$)/) || [first])[0];
  const line = sentence.length >= 20 ? sentence : first;
  const t = line.length > 140 ? line.slice(0, 139).replace(/\s+\S*$/, '') + '…' : line;
  if (!$env.CARDS_URL || !CARD_CHANNELS.includes(channel) || !t) {
    return { skip: true, method: 'GET', url: `${$env.CARDS_URL || $env.CALENDAR_URL}/health`, body: '{}' };
  }
  const accent = /^#[0-9a-fA-F]{6}$/.test(String(brand.accent_color || '')) ? brand.accent_color : null;
  return { skip: false, method: 'POST', url: `${$env.CARDS_URL}/cards`, body: JSON.stringify({
    title: t, brand: brand.name ? String(brand.name).slice(0, 40) : null, channel, accent }) };
}
"""

IMAGE_URL_OF = r"""
function imageUrlOf(req, res) {
  return !req.skip && res && res.statusCode === 201 && res.body && typeof res.body.url === 'string' ? res.body.url : null;
}
const NO_IMAGE_IG = 'No image: the title card (17-image-cards) could not be made, and Instagram cannot be published without one. Add an image_url before approving.';
"""


# ---------- video: a preview MP4 from 71 for every video_script draft

VIDEO_REQUEST = r"""
// A video_script draft gets a preview MP4 from 71 (POST $VIDEO_URL/render). Other formats,
// or no VIDEO_URL, make a harmless GET /health instead so the item still passes through.
function videoRequest(script) {
  if (!$env.VIDEO_URL || !script || typeof script !== 'object') {
    return { skip: true, method: 'GET', url: `${$env.VIDEO_URL || $env.CALENDAR_URL}/health`, body: '{}' };
  }
  const b = ($('Brand profile').first().json || {}).body;
  const brand = b && typeof b === 'object' ? b : {};
  const name = String(brand.name || '').trim().slice(0, 40);
  const accent = /^#[0-9a-fA-F]{6}$/.test(String(brand.accent_color || '')) ? brand.accent_color : null;
  const logo = name.split(/\s+/).map(w => (w.match(/[\p{L}\p{N}]/u) || [''])[0]).join('').slice(0, 3).toUpperCase();
  const voice = String($env.VIDEO_VOICE || '').trim();   // empty: the service's TTS_BACKEND
  return { skip: false, method: 'POST', url: `${$env.VIDEO_URL}/render`, body: JSON.stringify({
    script, brand: { name: name || null, accent, logo_text: logo || null },
    ...(voice ? { voice: { backend: voice } } : {}) }) };
}
"""

# Fail-soft: a video that could not be rendered is a note, never a lost draft.
VIDEO_RESULT = r"""
function videoResult(req, res) {
  if (!req || req.skip) return { video_url: null, poster_url: null, video_note: null };
  const b = res && res.body;
  const http = u => typeof u === 'string' && /^https?:\/\/\S+$/.test(u) && u.length <= 2000;
  if (res && res.statusCode === 201 && b && http(b.url)) {
    return { video_url: b.url, poster_url: http(b.poster_url) ? b.poster_url : null,
      video_note: `video preview: ${b.url} (${b.duration_s} s)` };
  }
  const d = b && typeof b === 'object' ? b.detail : b;
  const err = res && res.error;
  const why = typeof d === 'string' ? d
    : Array.isArray(d) ? d.map(x => [].concat((x && x.loc) || []).slice(1).join('.') + ' ' + ((x && x.msg) || '')).join('; ')
    : d ? JSON.stringify(d)
    : err ? (typeof err === 'string' ? err : err.message || JSON.stringify(err)) : 'no answer';
  const code = res && res.statusCode ? `HTTP ${res.statusCode}: ` : '';
  return { video_url: null, poster_url: null,
    video_note: `video not rendered: ${(code + String(why)).replace(/\s+/g, ' ').trim().slice(0, 300)}` };
}
"""

# The approval form (38) reads an edited video script back into the video_script JSON so 71
# can render it again. The text is what 57/65 wrote: "Hook: …\nOn screen: …\n\nSay: …\n
# On screen: …\n\nClose: …\n\nCaption: …\n#tags", then "Estimated length…" and "Shot list:".
# Pure: no $env, no $(); tests/video_script_parse_test.js runs it from the built workflow.json.
VIDEO_SCRIPT_PARSE = r"""
// -> { script } or { error }. oldBody (the item before the edit) supplies each beat's shot.
function parseVideoScript(text, oldBody) {
  const LABEL = /^(?:[*_]{1,2})?(hook|say|on[ -]?screen|close|cta|caption)(?:[*_]{1,2})?\s*:\s*(?:[*_]{1,2})?\s*(.*)$/i;
  const TAIL = /^(estimated length\b|shot list\s*:?\s*$)/i;
  const TAGS = /^(#[^\s#]+\s*)+$/;
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const add = (o, k, v) => { o[k] = clean((o[k] || '') + ' ' + v); };
  const all = String(text || '').replace(/\r\n?/g, '\n').split('\n').map(l => l.trim());
  const cut = all.findIndex(l => TAIL.test(l));
  const lines = cut < 0 ? all : all.slice(0, cut);           // the tail is not part of the video
  const top = { hook: null, cta: null, caption: null };
  const beats = [], hashtags = [];
  let cur = null, field = null;                               // field: [object, key]
  const seg = () => { const s = { spoken: '', on_screen: '' }; if (top.hook) beats.push(s); else top.hook = s; return s; };
  const labelAhead = (i, re) => { for (let j = i + 1; j < lines.length && lines[j]; j++) { const m = lines[j].match(LABEL); if (m) return re.test(m[1]); } return false; };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line) { field = null; continue; }                    // a blank line ends a field
    if (TAGS.test(line)) { hashtags.push(...line.split(/\s+/).map(h => h.replace(/^#+/, '')).filter(Boolean)); field = null; continue; }
    const m = line.match(LABEL);
    if (m) {
      const k = m[1].toLowerCase().replace(/[ -]/g, '');
      if (k === 'hook') {
        if (top.hook) return { error: 'the script has two "Hook:" lines' };
        cur = seg(); field = [cur, 'spoken'];
      } else if (k === 'say') {
        if (top.cta !== null) return { error: 'a "Say:" line comes after "Close:"' };
        if (!top.hook) return { error: 'the script does not start with the hook ("Hook:" line)' };
        cur = seg(); field = [cur, 'spoken'];
      } else if (k === 'onscreen') {
        if (top.cta !== null) return { error: 'an "On screen:" line comes after "Close:"' };
        if (!cur || cur.on_screen) cur = seg();               // a beat whose "Say:" label was removed
        field = [cur, 'on_screen'];
      } else if (k === 'close' || k === 'cta') {
        if (top.cta !== null) return { error: 'the script has two "Close:" lines' };
        top.cta = ''; cur = null; field = [top, 'cta'];
      } else {
        top.caption = top.caption || ''; cur = null; field = [top, 'caption'];
      }
      add(field[0], field[1], m[2]);
      continue;
    }
    // A line without a label: a line of the field above, or (after a blank line) the next part.
    if (field) {
      // Right under a spoken line, with no "On screen:" coming in this block: the on-screen text.
      if (field[1] === 'spoken' && cur && !cur.on_screen && field[0][field[1]] && !labelAhead(i, /^on[ -]?screen$/i)) field = [cur, 'on_screen'];
      add(field[0], field[1], line);
    } else if (top.caption !== null || top.cta !== null) {
      top.caption = top.caption || ''; cur = null; field = [top, 'caption']; add(top, 'caption', line);
    } else {
      cur = seg(); field = [cur, 'spoken']; add(cur, 'spoken', line);
    }
  }
  // "Close:" label removed: the last block is a lone line with no on-screen text.
  if (top.cta === null && beats.length > 1 && !beats[beats.length - 1].on_screen) top.cta = beats.pop().spoken;
  const hook = top.hook;
  if (!hook || !hook.spoken) return { error: 'no hook (the "Hook:" line)' };
  if (!hook.on_screen) return { error: 'the hook has no "On screen:" line' };
  if (!beats.length) return { error: 'no beats ("Say:" + "On screen:" lines)' };
  if (beats.length > 8) return { error: `${beats.length} beats: the video has at most 8` };
  for (const [i, b] of beats.entries()) {
    if (!b.spoken) return { error: `beat ${i + 1} has no "Say:" line` };
    if (!b.on_screen) return { error: `beat ${i + 1} has no "On screen:" line` };
  }
  if (!top.cta) return { error: 'no "Close:" line' };
  // Shots come from the old item's "Shot list": the same beat (same line) keeps its shot; with
  // the same number of beats, beat k keeps shot k; a new beat has none.
  let shots = [], old = [];
  if (oldBody) {
    const ol = String(oldBody).replace(/\r\n?/g, '\n').split('\n').map(l => l.trim());
    const s = ol.findIndex(l => /^shot list\s*:?\s*$/i.test(l));
    if (s >= 0) for (const l of ol.slice(s + 1)) { const x = l.match(/^-\s*beat\s*(\d+)\s*:\s*(.+)$/i); if (x) shots[Number(x[1]) - 1] = clean(x[2]); }
    const p = parseVideoScript(oldBody, null);
    old = p.script ? p.script.beats : [];
  }
  const same = (a, b) => a.toLowerCase() === b.toLowerCase();
  const out = beats.map((b, i) => {
    const j = old.findIndex(o => same(o.spoken, b.spoken) || same(o.on_screen, b.on_screen));
    const shot = j >= 0 ? shots[j] : beats.length === old.length ? shots[i] : null;
    return { spoken: b.spoken, on_screen: b.on_screen, shot: shot || null };
  });
  return { script: { hook: { spoken: hook.spoken, on_screen: hook.on_screen }, beats: out, cta: top.cta,
    caption: top.caption ? top.caption : null, hashtags } };
}
"""


def video_render(wf, name="Render video", pos=None):
    """POST {VIDEO_URL}/render (or GET /health for skipped items). Never stops the workflow."""
    return http(wf, name, "={{ $json.method }}", "={{ $json.url }}", "={{ $json.body }}", key=True,
                never_error=True, continue_on_fail=True, full_response=True, timeout=300000, pos=pos)


def image_card(wf, name="Image card"):
    """POST {CARDS_URL}/cards (or GET /health for skipped items). Never stops the workflow:
    a post without an image is fine, except on Instagram (the caller notes that)."""
    return http(wf, name, "={{ $json.method }}", "={{ $json.url }}", "={{ $json.body }}", key=True,
                never_error=True, continue_on_fail=True, full_response=True, timeout=30000)


REPLY_POSTS = r"""
const gate = $('Quality gate').all();
const parts = $input.all().map((saved, i) => {
  const g = gate[i].json;
  const s = saved.json;
  const head = `### ${g.channel} — calendar #${s.id} (${s.status})`;
  const notes = [];
  if (g.rewritten) notes.push(`auto-fixed: ${g.fixed.join('; ')}`);
  if (g.problems.length) notes.push(`needs a human: ${g.problems.join('; ')}`);
  if (g.warnings.length) notes.push(`warnings: ${g.warnings.join('; ')}`);
  return [head, g.text, notes.length ? `_${notes.join(' · ')}_` : ''].filter(Boolean).join('\n\n');
});
parts.push("Items marked in_review are waiting in the approval form; nothing is published until approved.");
return [{ json: { result: parts.join('\n\n') } }];
"""

KB_CONTEXT = r"""
const start = $('Start').first().json;
const results = ($input.first().json.results || []).filter(r => r.score >= 0.35);
const context = results.map((r, i) => `[${i + 1}] (${r.title}) ${r.chunk}`).join('\n');
return [{ json: { ...start, context } }];
"""

# --------------------------------------------------------------------------- 35 quality gate

CLAIM_TIMEOUT = 900000  # ms; long posts have many claims, each checked with several LLM calls

CLAIMS_BODY = "={{ JSON.stringify({ text: %s, context: $('Start').item.json.context || null }) }}"


def wf35():
    wf = Workflow(35, "Tool · Quality gate")
    s = sub_trigger(wf, [("text", "string"), ("channel", "string"), ("context", "string"), ("ref", "string"),
                         ("rewrite", "string"), ("links", "string")])
    b = http(wf, "Brand check", "POST", "={{ $env.BRAND_URL }}/check",
             "={{ JSON.stringify({ text: $json.text, channel: $json.channel, allowed_domains: String($json.links || '').split(/[\\s,]+/).filter(Boolean) }) }}",
             never_error=True, key=True)
    r = http(wf, "Platform rules", "POST", "={{ $env.RULES_URL }}/validate",
             "={{ JSON.stringify({ channel: $('Start').item.json.channel, text: $('Start').item.json.text }) }}",
             never_error=True)
    rd = http(wf, "Readability", "POST", "={{ $env.READABILITY_URL }}/score",
              "={{ JSON.stringify({ text: $('Start').item.json.text }) }}", never_error=True)
    cc = http(wf, "Claim check", "POST", "={{ $env.CLAIMS_URL }}/verify",
              CLAIMS_BODY % "$('Start').item.json.text", key=True, never_error=True, continue_on_fail=True,
              timeout=CLAIM_TIMEOUT)
    tq = quote_check(wf, "Testimonial check", "$('Start').item.json.text")
    c = code(wf, "Collect problems", r"""
const start = $('Start').item.json;
const brand = $('Brand check').item.json;
const rules = $('Platform rules').item.json;   // 422 for channels without limits (blog, email body)
const read = $('Readability').item.json;
const claims = $('Claim check').item.json;     // no "ok" field = checker down or failed
const quotes = $('Testimonial check').item.json;
const sev = s => v => (v || []).filter(x => (x.severity || 'error') === s);
// A 7B model fixes copy when told what to DO; diagnostic wording ("contains banned
// phrase 'x'") made it return the draft unchanged (0/6 vs 6/6 in local tests).
function instruction(v) {
  switch (v.rule) {
    case 'banned_phrase': return `Remove the banned claim "${v.match || v.detail}" (reword it without those words)`;
    case 'unsupported_claim': return `Remove the claim "${v.detail}" or reword it to match our approved facts (it is not in them)`;
    case 'missing_disclaimer': return `Add a disclosure: ${v.detail}`;
    case 'too_long': return `Shorten to at most ${rules.limit} characters (it is ${rules.length})`;
    case 'too_many_hashtags': return `Use fewer hashtags (${v.detail})`;
    case 'exclamation_marks': return 'Use at most one exclamation mark';
    case 'all_caps': return 'Write words in normal case, not ALL CAPS';
    case 'too_many_emojis': return `Use fewer emojis (${v.detail})`;
    case 'emoji_not_allowed': return `Remove the emoji ${v.match || ''} (it is not one of the brand's emojis)`;
    case 'above_recommended': return `Make it shorter (${v.detail})`;
    case 'fake_testimonial': return `Remove the quotation "${v.match}" and its quotation marks: it is not a real customer quote with consent (never invent or paraphrase a customer quote)`;
    default: return `Fix: ${v.detail}`;
  }
}
const claimErrors = (claims.unsupported || []).map(u => ({ rule: 'unsupported_claim', detail: u, severity: 'error' }));
// Every quotation must be a real, consented testimonial, word for word (58; FTC rule on fake
// reviews and testimonials). Skipped when REVIEWS_URL is empty (the node then hit /health).
const quoteErrors = $env.REVIEWS_URL ? (quotes.violations || []).map(q => ({ rule: 'fake_testimonial',
  match: q.text, detail: `quote is not a real testimonial with consent: "${q.text}"`, severity: 'error' })) : [];
const errors = [...sev('error')(brand.violations), ...sev('error')(rules.violations), ...claimErrors, ...quoteErrors];
const warns = [...sev('warn')(brand.violations), ...sev('warn')(rules.violations)];
const warnings = warns.map(v => v.detail);
if (typeof claims.ok !== 'boolean') warnings.push('claims NOT fact-checked (claim checker unavailable)');
if ($env.REVIEWS_URL && typeof quotes.ok !== 'boolean') warnings.push('quotes NOT checked against the testimonials (review hub unavailable)');
return { json: {
  ref: start.ref || null,
  text: start.text, channel: start.channel, context: start.context || null, limit: rules.limit ?? null,
  problems: errors.map(v => v.rule === 'unsupported_claim' ? `unsupported claim: ${v.detail}` : v.detail),
  warnings, instructions: [...errors, ...warns].map(instruction),
  readability: read.verdict ?? null, needs_fix: errors.length > 0,
} };
""", each=True)
    # rewrite "no": report problems only. The format tool (57) uses it: rewriting a structured
    # script or email sequence as one text merged its parts (night 5).
    i = if_true(wf, "Needs fix?", "={{ $json.needs_fix && $('Start').item.json.rewrite !== 'no' }}")
    rw = gateway(wf, "Rewrite", "rewrite_to_fix",
                 "{ text: $json.text, problems: $json.instructions.map(p => '- ' + p).join('\\n'), "
                 "channel: $json.channel, limit: $json.limit }", pos=[wf._x, 200])
    rb = http(wf, "Recheck brand", "POST", "={{ $env.BRAND_URL }}/check",
              "={{ JSON.stringify({ text: $json.output.text, channel: $('Collect problems').item.json.channel, allowed_domains: String($('Start').item.json.links || '').split(/[\\s,]+/).filter(Boolean) }) }}",
              never_error=True, pos=[wf._x + 240, 200], key=True)
    rr = http(wf, "Recheck rules", "POST", "={{ $env.RULES_URL }}/validate",
              "={{ JSON.stringify({ channel: $('Collect problems').item.json.channel, text: $('Rewrite').item.json.output.text }) }}",
              never_error=True, pos=[wf._x + 480, 200])
    rc = http(wf, "Recheck claims", "POST", "={{ $env.CLAIMS_URL }}/verify",
              CLAIMS_BODY % "$('Rewrite').item.json.output.text", key=True, never_error=True, continue_on_fail=True,
              timeout=CLAIM_TIMEOUT, pos=[wf._x + 720, 200])
    rq = quote_check(wf, "Recheck quotes", "$('Rewrite').item.json.output.text", pos=[wf._x + 960, 200])
    fx = code(wf, "Fixed", r"""
const prev = $('Collect problems').item.json;
const text = $('Rewrite').item.json.output.text;
const b = $('Recheck brand').item.json;
const r = $('Recheck rules').item.json;
const cl = $('Recheck claims').item.json;
const q = $('Recheck quotes').item.json;
const errors = v => (v || []).filter(x => (x.severity || 'error') === 'error');
const remaining = [
  ...errors(b.violations).map(v => `brand: ${v.detail}`),
  ...errors(r.violations).map(v => `${prev.channel}: ${v.detail}`),
  ...(cl.unsupported || []).map(u => `unsupported claim: ${u}`),
  ...($env.REVIEWS_URL ? (q.violations || []).map(v => `quote is not a real testimonial with consent: "${v.text}"`) : []),
];
const warns = v => (v || []).filter(x => x.severity === 'warn').map(x => x.detail);
const warnings = [...warns(b.violations), ...warns(r.violations)];
if (typeof cl.ok !== 'boolean') warnings.push('claims NOT fact-checked (claim checker unavailable)');
if ($env.REVIEWS_URL && typeof q.ok !== 'boolean') warnings.push('quotes NOT checked against the testimonials (review hub unavailable)');
return { json: { ref: prev.ref, ok: remaining.length === 0, text, channel: prev.channel, rewritten: true,
  fixed: prev.problems, problems: remaining, warnings, readability: prev.readability } };
""", each=True, pos=[wf._x + 1200, 200])
    ps = code(wf, "Passed", r"""
const p = $json;
// Also reached with problems when the caller asked for rewrite "no": report them, never "ok".
// (Night 5: a video script with a banned emoji and an unsupported claim went to review as ok.)
return { json: { ref: p.ref, ok: p.problems.length === 0, text: p.text, channel: p.channel, rewritten: false,
  fixed: [], problems: p.problems, warnings: p.warnings, readability: p.readability } };
""", each=True, pos=[wf._x, 420])
    m = merge_append(wf, "Result", pos=[wf._x + 1440, 300])
    wf.chain(s, b, r, rd, cc, tq, c, i)
    wf.link(i, rw, src_index=0)
    wf.link(i, ps, src_index=1)
    wf.chain(rw, rb, rr, rc, rq, fx)
    wf.link(fx, m, dst_index=0)
    wf.link(ps, m, dst_index=1)
    return wf, {
        "summary": "Checks a piece of copy against the brand rules (05), the platform limits (14), readability (13), the approved facts (claim checker, 44) and, when `REVIEWS_URL` is set, the proof bank (58): every quotation in double quotes must be a real testimonial with consent, word for word, or it is an error (the FTC rule on fake reviews and testimonials, 16 CFR Part 465). If there are errors (including claims the facts don't support), it asks the LLM for a minimal rewrite and checks everything again. What still fails goes back as `problems` for a human.",
        "inputs": {"links": "URLs the writer was given (comma/space separated); their domains may appear in the copy. Any other domain except the brand's is flagged", "rewrite": "`no` = only report problems, don't rewrite (structured formats); empty = rewrite", "text": "copy to check", "channel": "x, linkedin, instagram, facebook, threads, mastodon, blog, email, google_ads …",
                   "context": "optional: the brief or source the copy was written from; facts in it count as evidence",
                   "ref": "optional: any id you want back on the result (e.g. a calendar item id)"},
        "output": "`{ref, ok, text, channel, rewritten, fixed[], problems[], warnings[], readability}` for each input item. Items come back in a different order than they went in (fixed ones first), so match results by `ref`",
        "depends": ["05-brand-service", "13-readability", "14-platform-rules", "44-claim-checker", "03-llm-gateway",
                    "58-review-hub (optional: skipped when `REVIEWS_URL` is empty)"],
        "called_by": "25, 26, 28, 30 and the chat agent (24, tool `check_copy`)",
        "test": {"text": "Our Desk Blend has notes of chocolate and caramel and is roasted within 24 hours. https://example.com", "channel": "x", "context": ""},
    }

# --------------------------------------------------------------------------- 25 blog

def wf25():
    wf = Workflow(25, "Tool · Blog writer")
    s = sub_trigger(wf, [("topic", "string"), ("audience", "string"), ("keywords", "string")])
    kb = http(wf, "Search knowledge base", "POST", "={{ $env.KB_URL }}/search",
              "={{ JSON.stringify({ query: $json.topic, k: 4 }) }}", continue_on_fail=True)
    ctx = code(wf, "Build context", KB_CONTEXT)
    g = gateway(wf, "Write blog post", "blog_post",
                "{ topic: $json.topic, audience: $json.audience || null, keywords: $json.keywords || null, context: $json.context || null }")
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.output }}", "channel": "blog",
                      "context": "={{ [$('Start').first().json.topic, $('Build context').first().json.context].filter(Boolean).join('\\n') }}"})
    sv = http(wf, "Save draft", "POST", "={{ $env.CALENDAR_URL }}/items",
              SAVE_ITEM_BODY % ("(($json.text.match(/^#\\s+(.+)$/m) || [])[1] || $('Start').first().json.topic).slice(0, 120)",
                                "null", "null"), key=True)
    rp = code(wf, "Reply", r"""
const g = $('Quality gate').first().json;
const s = $input.first().json;
const notes = [`Saved to the content calendar as #${s.id} (${s.status}). Readability: ${g.readability}.`];
if (g.rewritten) notes.push(`Auto-fixed: ${g.fixed.join('; ')}`);
if (g.problems.length) notes.push(`Needs a human: ${g.problems.join('; ')}`);
if (g.warnings.length) notes.push(`Warnings: ${g.warnings.join('; ')}`);
return [{ json: { result: notes.join('\n') + '\n\n' + g.text } }];
""")
    wf.chain(s, kb, ctx, g, q, sv, rp)
    return wf, {
        "summary": "Writes a markdown blog post. It pulls brand facts from the knowledge base (06), writes through the gateway (03), runs the quality gate (35) and saves the post to the calendar (19).",
        "inputs": {"topic": "what the post is about", "audience": "optional", "keywords": "optional, comma-separated"},
        "output": "`{result}`: a calendar reference, the quality notes and the full post",
        "depends": ["03-llm-gateway", "06-knowledge-base", "19-content-calendar", "35-wf-tool-quality-gate"],
        "called_by": "the chat agent (24), tool `write_blog_post`",
        "test": {"topic": "How to stay focused working from home", "audience": "remote engineers", "keywords": ""},
    }

# --------------------------------------------------------------------------- 26 social

def wf26():
    wf = Workflow(26, "Tool · Social post writer")
    s = sub_trigger(wf, [("topic", "string"), ("channels", "string"), ("link", "string"), ("campaign", "string")])
    p = code(wf, "Prepare", NORMALIZE_CHANNELS + r"""
const s = $input.first().json;
return [{ json: { topic: s.topic, channels: normalizeChannels(s.channels),
  link: (s.link || '').trim(), campaign: (s.campaign || 'always-on').trim() } }];
""")
    # Closed loop: 45 ranks hook styles by clicks per post (Thompson sampling) and names two
    # to prefer. If 45 is down or slow, the posts are written without preferences.
    bp = brand_profile(wf)
    hk = http(wf, "Hook preferences", "GET", "={{ $env.CAMPAIGNS_URL }}/insights/hooks", query={"days": "90"},
              never_error=True, continue_on_fail=True, full_response=True, timeout=15000)
    ex = http(wf, "Approved examples", "GET", "={{ $env.LEARNING_URL }}/examples", query={"k": "3", "by": "performance"},
              never_error=True, continue_on_fail=True, full_response=True)
    g = gateway(wf, "Write posts", "social_posts",
                "{ topic: $('Prepare').first().json.topic, channels: $('Prepare').first().json.channels.join(', '), "
                "link: $('Prepare').first().json.link || null, campaign: $('Prepare').first().json.campaign, "
                "examples: (Array.isArray($json.body) ? $json.body : []).filter(e => e && e.text).map(e => '- (' + e.channel + ') ' + e.text).join('\\n') || null, "
                "prefer_hooks: [].concat((($('Hook preferences').first().json || {}).body || {}).recommended || []).filter(h => typeof h === 'string').join(', ') || null }")
    sp = code(wf, "Split posts", SPLIT_POSTS)
    u = http(wf, "UTM link", "POST", "={{ $env.UTM_URL }}/build",
             "={{ JSON.stringify({ url: $('Prepare').first().json.link || 'none', source: $json.channel, medium: 'social', campaign: $('Prepare').first().json.campaign }) }}",
             never_error=True)
    au = code(wf, "Apply UTM", APPLY_UTM, each=True)
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.text }}", "channel": "={{ $json.channel }}",
                      "context": "={{ $('Prepare').first().json.topic }}", "links": "={{ $('Prepare').first().json.link || '' }}"})
    # hook_style of the post for this channel, straight from the model output (Split posts
    # is shared with 30 and keeps only channel/text), so 45 can learn which hooks earn clicks.
    hook_js = ("hook_style: ((($('Write posts').first().json.output || {}).posts || [])"
               ".find(x => String(x.channel || '').trim().toLowerCase() === $json.channel) || {}).hook_style || null, ")
    save_body = SAVE_ITEM_BODY % ("($('Prepare').first().json.topic + ' (' + $json.channel + ')').slice(0, 120)",
                                  "$('Prepare').first().json.campaign", "$('Prepare').first().json.link || null")
    cr = code(wf, "Card request", CARD_REQUEST + r"""
return { json: { ...cardRequest($json.channel, $json.text), post: $json } };
""", each=True)
    ic = image_card(wf)
    ai = code(wf, "Attach image", IMAGE_URL_OF + r"""
const req = $('Card request').item.json;
const g = req.post;
const image_url = imageUrlOf(req, $json);
const notes = [g.problems.length ? 'Quality gate: ' + g.problems.join('; ') : null,
  !image_url && g.channel === 'instagram' ? NO_IMAGE_IG : null].filter(Boolean).join('\n') || null;
return { json: { ...g, image_url, notes } };
""", each=True)
    old_notes = "notes: $json.problems.length ? 'Quality gate: ' + $json.problems.join('; ') : null"
    assert old_notes in save_body
    save_body = save_body.replace(old_notes, "image_url: $json.image_url, notes: $json.notes")
    sv = http(wf, "Save drafts", "POST", "={{ $env.CALENDAR_URL }}/items",
              save_body.replace("status: $json.ok", hook_js + "status: $json.ok", 1), key=True)
    rp = code(wf, "Reply", REPLY_POSTS.replace(
        "  if (g.warnings.length)",
        "  if (s.image_url) notes.push(`image: ${s.image_url}`);\n"
        "  else if (g.channel === 'instagram') notes.push('no image: Instagram cannot be published without one');\n"
        "  if (g.warnings.length)", 1))
    wf.chain(s, p, bp, hk, ex, g, sp, u, au, q, cr, ic, ai, sv, rp)
    return wf, {
        "summary": "Writes one native post per channel, in the style of up to 3 of your approved posts (46): first those that earned clearly more clicks than a typical post of their channel (tracked links, 45), the rest the ones you approved or edited most recently. It removes channels nobody asked for, makes sure the link is present, tags the link with UTM parameters per channel (15), runs every post through the quality gate (35) and saves each post to the calendar (19). It also learns which opening works: each post is tagged with a hook style (question, fact_led, story, how_to, benefit, contrarian) that is saved on its calendar item; once published, clicks on its tracked link come back through the campaign service (45), whose `/insights/hooks` ranks the styles by clicks per post (Thompson sampling, with some exploration of little-tried styles). Before writing, this workflow asks 45 for the two styles to prefer and passes them to the prompt as `prefer_hooks`. If 45 is unreachable, posts are written without preferences. Posts for instagram, facebook, threads, linkedin and x also get a title card from 17 (`POST $CARDS_URL/cards`: the post's first line as title, the brand name from 05, square or OG size by channel), saved as the item's `image_url`. If the card fails the post is saved without an image; an Instagram post then gets a note, because Instagram cannot be published without one.",
        "inputs": {"topic": "what to post about", "channels": "e.g. `x, linkedin, instagram`", "link": "optional URL", "campaign": "optional; UTM campaign, default `always-on`"},
        "output": "`{result}`: each post with its calendar id and quality notes",
        "depends": ["03-llm-gateway", "05-brand-service", "15-utm-builder", "17-image-cards", "19-content-calendar", "35-wf-tool-quality-gate", "45-campaign-service"],
        "called_by": "the chat agent (24), tool `write_social_posts`",
        "test": {"topic": "Our new decaf, roasted in small batches", "channels": "x, linkedin", "link": "https://example.com/decaf", "campaign": "decaf launch"},
    }

# --------------------------------------------------------------------------- 27 ads

def wf27():
    wf = Workflow(27, "Tool · Ad copy")
    s = sub_trigger(wf, [("product", "string"), ("offer", "string"), ("audience", "string"), ("keywords", "string")])
    g = gateway(wf, "Write ad copy", "ad_copy",
                "{ product: $json.product, offer: $json.offer || null, audience: $json.audience || null, keywords: $json.keywords || null }")
    b = http(wf, "Brand check", "POST", "={{ $env.BRAND_URL }}/check",
             "={{ JSON.stringify({ text: [...$json.output.headlines, ...$json.output.descriptions].join('\\n'), channel: 'paid_search' }) }}",
             never_error=True, key=True)
    f = code(wf, "Format", r"""
const ads = $('Write ad copy').first().json.output;
const brand = $input.first().json;
const errors = (brand.violations || []).filter(v => v.severity === 'error').map(v => v.detail);
const body = [
  'Headlines (max 30):', ...ads.headlines.map(h => `- ${h} (${h.length})`),
  '', 'Descriptions (max 90):', ...ads.descriptions.map(d => `- ${d} (${d.length})`),
].join('\n');
return [{ json: { body, errors, product: $('Start').first().json.product } }];
""")
    sv = http(wf, "Save draft", "POST", "={{ $env.CALENDAR_URL }}/items",
              "={{ JSON.stringify({ title: ('Search ads: ' + $json.product).slice(0, 120), channel: 'google_ads', body: $json.body, status: $json.errors.length ? 'draft' : 'in_review', notes: $json.errors.length ? 'Brand: ' + $json.errors.join('; ') : null }) }}",
              key=True)
    rp = code(wf, "Reply", r"""
const f = $('Format').first().json;
const s = $input.first().json;
const warn = f.errors.length ? `\n\nBrand problems to fix by hand: ${f.errors.join('; ')}` : '';
return [{ json: { result: `Saved as calendar #${s.id} (${s.status}).\n\n${f.body}${warn}` } }];
""")
    wf.chain(s, g, b, f, sv, rp)
    return wf, {
        "summary": "Writes responsive search ad assets. Google's length limits are enforced by the gateway's schema, and the copy gets a brand check before it's saved to the calendar.",
        "inputs": {"product": "what is advertised", "offer": "optional", "audience": "optional", "keywords": "optional"},
        "output": "`{result}`: headlines and descriptions with character counts",
        "depends": ["03-llm-gateway", "05-brand-service", "19-content-calendar"],
        "called_by": "the chat agent (24), tool `write_ad_copy`",
        "test": {"product": "Coffee subscription for remote workers", "offer": "First bag free", "audience": "", "keywords": ""},
    }

# --------------------------------------------------------------------------- 28 email

def wf28():
    wf = Workflow(28, "Tool · Email newsletter")
    s = sub_trigger(wf, [("topic", "string"), ("audience", "string"), ("cta_url", "string")])
    kb = http(wf, "Search knowledge base", "POST", "={{ $env.KB_URL }}/search",
              "={{ JSON.stringify({ query: $json.topic, k: 4 }) }}", continue_on_fail=True)
    ctx = code(wf, "Build context", KB_CONTEXT)
    g = gateway(wf, "Write email", "email_newsletter",
                "{ topic: $json.topic, audience: $json.audience || null, context: $json.context || null }")
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.output.body_markdown }}", "channel": "email",
                      "context": "={{ [$('Start').first().json.topic, $('Build context').first().json.context].filter(Boolean).join('\\n') }}",
                      "links": "={{ $('Start').first().json.cta_url || '' }}"})
    sj = http(wf, "Subject check", "POST", "={{ $env.RULES_URL }}/validate",
              "={{ JSON.stringify({ channel: 'email_subject', text: $('Write email').first().json.output.subject }) }}",
              never_error=True)
    rn = http(wf, "Render HTML", "POST", "={{ $env.EMAIL_RENDER_URL }}/render",
              "={{ JSON.stringify({ subject: $('Write email').first().json.output.subject, preheader: $('Write email').first().json.output.preheader, body_markdown: $('Quality gate').first().json.text, cta_text: $('Start').first().json.cta_url ? $('Write email').first().json.output.cta_text : null, cta_url: $('Start').first().json.cta_url || null }) }}")
    sv = http(wf, "Save draft", "POST", "={{ $env.CALENDAR_URL }}/items",
              "={{ JSON.stringify({ title: ('Email: ' + $('Write email').first().json.output.subject).slice(0, 120), channel: 'email', body: 'Subject: ' + $('Write email').first().json.output.subject + '\\nPreheader: ' + $('Write email').first().json.output.preheader + '\\n\\n' + $('Quality gate').first().json.text, status: $('Quality gate').first().json.ok ? 'in_review' : 'draft', link: $('Start').first().json.cta_url || null }) }}",
              key=True)
    rp = code(wf, "Reply", r"""
const e = $('Write email').first().json.output;
const g = $('Quality gate').first().json;
const subj = $('Subject check').first().json;
const s = $input.first().json;
const notes = [];
if (g.rewritten) notes.push(`auto-fixed: ${g.fixed.join('; ')}`);
if (g.problems.length) notes.push(`needs a human: ${g.problems.join('; ')}`);
for (const v of subj.violations || []) notes.push(`subject: ${v.detail}`);
return [{ json: { result: [
  `Saved as calendar #${s.id} (${s.status}). HTML version rendered (${$('Render HTML').first().json.html.length} chars).`,
  `**Subject:** ${e.subject}`, `**Preheader:** ${e.preheader}`, '', g.text, '', `CTA: ${e.cta_text}`,
  notes.length ? `_${notes.join(' · ')}_` : '',
].join('\n') } }];
""")
    wf.chain(s, kb, ctx, g, q, sj, rn, sv, rp)
    return wf, {
        "summary": "Writes a newsletter: subject, preheader, body and CTA. It uses brand facts from the knowledge base, runs the quality gate on the body, checks the subject length, renders email-safe HTML (18) and saves a draft to the calendar.",
        "inputs": {"topic": "what the email is about", "audience": "optional", "cta_url": "optional button link"},
        "output": "`{result}`: subject, preheader, body, quality notes",
        "depends": ["03-llm-gateway", "06-knowledge-base", "14-platform-rules", "18-email-renderer", "19-content-calendar", "35-wf-tool-quality-gate"],
        "called_by": "the chat agent (24), tool `write_email`",
        "test": {"topic": "Our autumn roast lineup", "audience": "subscribers", "cta_url": "https://example.com/autumn"},
    }

# --------------------------------------------------------------------------- 29 seo brief

def wf29():
    wf = Workflow(29, "Tool · SEO brief")
    s = sub_trigger(wf, [("keyword", "string"), ("competitor_url", "string"), ("audience", "string")])
    k = http(wf, "Related searches", "POST", "={{ $env.KEYWORDS_URL }}/suggest",
             "={{ JSON.stringify({ seed: $json.keyword, expand: false }) }}", never_error=True, continue_on_fail=True)
    a = http(wf, "Audit competitor", "POST", "={{ $env.SEO_URL }}/audit",
             "={{ JSON.stringify({ url: $('Start').first().json.competitor_url || 'none', keyword: $('Start').first().json.keyword }) }}",
             never_error=True, continue_on_fail=True)
    p = code(wf, "Prepare", r"""
const s = $('Start').first().json;
const kw = ($('Related searches').first().json.keywords || []).map(k => k.keyword).slice(0, 25);
const audit = $input.first().json;
let notes = null;
if (s.competitor_url && Array.isArray(audit.checks)) {
  const issues = audit.checks.filter(c => c.status !== 'pass').map(c => `- ${c.status}: ${c.message}`);
  notes = `${s.competitor_url} scored ${audit.score}/100.\n${issues.join('\n')}`;
}
return [{ json: { keyword: s.keyword, audience: s.audience || null, related: kw.join('\n') || null, notes } }];
""")
    g = gateway(wf, "Write brief", "seo_brief",
                "{ keyword: $json.keyword, related_keywords: $json.related, competitor_notes: $json.notes, audience: $json.audience }")
    f = code(wf, "Format", r"""
const b = $input.first().json.output;
const lines = [
  `# SEO brief: ${$('Prepare').first().json.keyword}`,
  `**Intent:** ${b.search_intent} · **Target length:** ~${b.target_word_count} words`,
  '', '**Title options**', ...b.title_options.map(t => `- ${t} (${t.length})`),
  '', `**Meta description** (${b.meta_description.length}): ${b.meta_description}`,
  '', '**Outline**', ...b.outline.flatMap(o => [`## ${o.h2}`, ...o.points.map(p => `- ${p}`)]),
  '', '**FAQs**', ...b.faqs.map(f => `- **${f.q}** ${f.a}`),
];
// Only the prose goes to the claim checker, one statement per line: not the "(54)" length
// counts or the word count, which are numbers the facts can't contain.
const stop = s => { s = String(s || '').trim(); return !s || /[.!?]$/.test(s) ? s : s + '.'; };
const prose = [...b.title_options, b.meta_description, ...b.outline.flatMap(o => [o.h2, ...o.points]),
  ...b.faqs.map(f => f.a)].map(stop).filter(Boolean);
return [{ json: { result: lines.join('\n'), check_text: prose.join('\n') } }];
""")
    # Briefs invented facts ("locally roasted", a 1:16 ratio when the page says 1:15), so the
    # prose is fact-checked (44) and flagged, never removed. Evidence: approved facts plus the
    # inputs. The audited page is not evidence: it may be a competitor's (audit H1).
    cc = http(wf, "Claim check", "POST", "={{ $env.CLAIMS_URL }}/verify",
              "={{ JSON.stringify({ text: $json.check_text, context: ['Target keyword: ' + $('Start').first().json.keyword, "
              "$('Start').first().json.audience ? 'Audience: ' + $('Start').first().json.audience : ''].filter(Boolean).join('\\n') }) }}",
              key=True, continue_on_fail=True, timeout=1800000)
    an = code(wf, "Annotate", r"""
const brief = $('Format').first().json.result;
const r = $input.first().json;
if (!Array.isArray(r.unsupported)) {
  const why = String((r.error && (r.error.message || r.error)) || r.detail || 'no answer').slice(0, 200);
  return [{ json: { result: `${brief}\n\n_Claims were not fact-checked (claim checker: ${why})._` } }];
}
if (!r.unsupported.length) return [{ json: { result: brief } }];
const reasons = new Map((r.claims || []).filter(c => !c.supported).map(c => [c.claim, c.reasons || []]));
const list = r.unsupported.map(u => {
  const why = (reasons.get(u) || []).join('; ');
  return `- ${u}${why ? ` (${why})` : ''}`;
});
const n = r.unsupported.length;
return [{ json: { result: [
  `> ⚠ ${n} statement${n === 1 ? '' : 's'} in this brief ${n === 1 ? 'is' : 'are'} not in the approved facts: check them before writing (list at the end).`,
  '', brief, '', '## Claims to check before writing', '',
  'Not supported by the approved facts (05) or the brief inputs. Verify or drop them in the article.', '',
  ...list,
].join('\n') } }];
""")
    wf.chain(s, k, a, p, g, f, cc, an)
    return wf, {
        "summary": "Builds an SEO content brief from real autocomplete searches (10) and, if you give one, an audit of a competitor page (12). The brief's prose (titles, meta description, outline, FAQ answers) is then fact-checked by the claim checker (44) against the approved facts and the inputs (keyword, audience; not the audited page, which may be a competitor's). Nothing is removed: if any statement is unsupported, the brief starts with a one-line warning and ends with a `## Claims to check before writing` list (each statement and why). If the checker is down, the brief says it was not checked.",
        "inputs": {"keyword": "target keyword", "competitor_url": "optional page that ranks today", "audience": "optional"},
        "output": "`{result}`: markdown brief (intent, titles, meta, outline, FAQs; plus the claims to check, if any)",
        "depends": ["03-llm-gateway", "10-keyword-suggest", "12-seo-auditor", "44-claim-checker"],
        "called_by": "the chat agent (24), tool `seo_brief`",
        "test": {"keyword": "coffee subscription for remote workers", "competitor_url": "", "audience": ""},
    }

# --------------------------------------------------------------------------- 30 repurpose

def wf30():
    wf = Workflow(30, "Tool · Repurpose content")
    # `note` (optional) is written as the first line of each saved draft's notes; the winner
    # recycler (59) uses it to record which post a draft recycles. Empty = notes as before.
    s = sub_trigger(wf, [("url", "string"), ("text", "string"), ("channels", "string"), ("link", "string"),
                         ("note", "string")])
    x = http(wf, "Fetch page", "POST", "={{ $env.EXTRACTOR_URL }}/extract",
             "={{ JSON.stringify({ url: $json.url || 'none' }) }}", never_error=True)
    p = code(wf, "Prepare", NORMALIZE_CHANNELS + r"""
const s = $('Start').first().json;
const page = $input.first().json;
const source = (s.text || '').trim() || (page.text || '').trim();
if (!source) throw new Error(s.url ? `Could not read ${s.url}: ${JSON.stringify(page.detail || page)}` : 'Give a url or text to repurpose');
return [{ json: { source, title: page.title || null, channels: normalizeChannels(s.channels),
  link: (s.link || s.url || '').trim(), campaign: 'repurpose' } }];
""")
    g = gateway(wf, "Write posts", "repurpose",
                "{ source_text: $json.source, source_title: $json.title, channels: $json.channels.join(', '), link: $json.link || null }")
    sp = code(wf, "Split posts", SPLIT_POSTS)
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.text }}", "channel": "={{ $json.channel }}",
                      "context": "",  # the scraped page is NOT evidence (audit H1: fact laundering)
                      "links": "={{ [$('Start').first().json.url, $('Start').first().json.link].filter(Boolean).join(' ') }}"})
    gate_note = "$json.problems.length ? 'Quality gate: ' + $json.problems.join('; ') : null"
    save_body = SAVE_ITEM_BODY % ("('Repurposed: ' + ($('Prepare').first().json.title || 'content') + ' (' + $json.channel + ')').slice(0, 120)",
                                  "'repurpose'", "$('Prepare').first().json.link || null")
    assert gate_note in save_body
    save_body = save_body.replace(gate_note, "[String($('Start').first().json.note || '').trim(), " + gate_note.replace(": null", ": ''")
                                  + "].filter(Boolean).join('\\n') || null", 1)
    sv = http(wf, "Save drafts", "POST", "={{ $env.CALENDAR_URL }}/items", save_body, key=True)
    rp = code(wf, "Reply", REPLY_POSTS)
    wf.chain(s, x, p, g, sp, q, sv, rp)
    return wf, {
        "summary": "Turns an article, a web page (fetched with 07) or pasted text into one grounded post per channel. Each post goes through the quality gate and is saved to the calendar.",
        "inputs": {"url": "page to repurpose (or leave empty and give text)", "text": "optional pasted content", "channels": "e.g. `x, linkedin`", "link": "optional link to include (defaults to url)",
                   "note": "optional; written as the first line of each saved draft's notes (the winner recycler, 59, records `recycled from #<id>` here)"},
        "output": "`{result}`: posts with calendar ids",
        "depends": ["03-llm-gateway", "07-page-extractor", "19-content-calendar", "35-wf-tool-quality-gate"],
        "called_by": "the chat agent (24), tool `repurpose_content`; the winner recycler (59)",
        "test": {"url": "", "text": "Remote workers lose focus after lunch. A lighter roast feels less heavy after a meal. We tested three brew ratios and 1:16 was the favourite.", "channels": "x, linkedin", "link": "https://example.com/afternoon", "note": ""},
    }

# --------------------------------------------------------------------------- 31 research url

def wf31():
    wf = Workflow(31, "Tool · Research a URL")
    s = sub_trigger(wf, [("url", "string"), ("focus", "string")])
    x = http(wf, "Fetch page", "POST", "={{ $env.EXTRACTOR_URL }}/extract", "={{ JSON.stringify({ url: $json.url }) }}")
    g = gateway(wf, "Analyse", "summarize_page",
                "{ url: $('Start').first().json.url, title: $json.title, text: $json.text, focus: $('Start').first().json.focus || null }")
    f = code(wf, "Format", r"""
const a = $input.first().json.output;
const list = (title, xs) => xs.length ? [`**${title}**`, ...xs.map(x => `- ${x}`), ''] : [];
return [{ json: { result: [
  `# ${$('Fetch page').first().json.title || $('Start').first().json.url}`,
  $('Start').first().json.url, '', a.summary, '', `**Audience:** ${a.audience || 'not stated'}`, '',
  ...list('Key messages', a.key_messages), ...list('Offers & pricing', a.offers_pricing),
  ...list('Strengths', a.strengths), ...list('Weaknesses', a.weaknesses),
  ...list('What we could do', a.opportunities_for_us),
].join('\n') } }];
""")
    wf.chain(s, x, g, f)
    return wf, {
        "summary": "Reads a web page (07) and returns a competitive analysis: messages, pricing, strengths, weaknesses and opportunities for your brand.",
        "inputs": {"url": "page to analyse", "focus": "optional, e.g. `pricing`"},
        "output": "`{result}`: markdown analysis",
        "depends": ["03-llm-gateway", "07-page-extractor"],
        "called_by": "the chat agent (24), tool `research_url`",
        "test": {"url": "https://example.com", "focus": ""},
    }

# --------------------------------------------------------------------------- 32 keyword research

def wf32():
    wf = Workflow(32, "Tool · Keyword research")
    s = sub_trigger(wf, [("seed", "string")])
    k = http(wf, "Get suggestions", "POST", "={{ $env.KEYWORDS_URL }}/suggest",
             "={{ JSON.stringify({ seed: $json.seed, expand: true }) }}")
    p = code(wf, "Shortlist", r"""
const all = ($input.first().json.keywords || []).map(k => k.keyword);
if (!all.length) throw new Error('No suggestions came back for this seed');
// Keep the list small enough for a 7B model to cluster reliably.
return [{ json: { seed: $('Start').first().json.seed, total: all.length, keywords: all.slice(0, 60).join('\n') } }];
""")
    g = gateway(wf, "Cluster", "keyword_clusters", "{ seed: $json.seed, keywords: $json.keywords }")
    f = code(wf, "Format", r"""
const c = $input.first().json.output.clusters;
const p = $('Shortlist').first().json;
const order = { high: 0, medium: 1, low: 2 };
c.sort((a, b) => order[a.relevance] - order[b.relevance]);
return [{ json: { result: [
  `# Keyword clusters for "${p.seed}" (${p.total} real suggestions, 60 clustered)`, '',
  ...c.flatMap(x => [`## ${x.name} — ${x.intent}, relevance ${x.relevance}`,
    x.keywords.map(k => '`' + k + '`').join(', '), `Idea: ${x.content_idea}`, '']),
].join('\n') } }];
""")
    wf.chain(s, k, p, g, f)
    return wf, {
        "summary": "Gets real Google and DuckDuckGo autocomplete suggestions for a seed (10), then has the LLM group them by search intent with a content idea for each group.",
        "inputs": {"seed": "seed keyword"},
        "output": "`{result}`: markdown clusters",
        "depends": ["03-llm-gateway", "10-keyword-suggest"],
        "called_by": "the chat agent (24), tool `keyword_research`",
        "test": {"seed": "coffee subscription"},
    }

# --------------------------------------------------------------------------- 33 calendar

def wf33():
    wf = Workflow(33, "Tool · Content calendar")
    s = sub_trigger(wf, [("action", "string"), ("id", "string"), ("status", "string"), ("channel", "string"),
                         ("title", "string"), ("body", "string"), ("scheduled_at", "string")])
    cur = http(wf, "Current item", "GET",
               "={{ /^(#|item\\s*)?\\d{1,9}$/i.test(String($json.id || '').trim()) ? $env.CALENDAR_URL + '/items/' + String($json.id).replace(/[^0-9]/g, '') : $env.CALENDAR_URL + '/health' }}",
               key=True, never_error=True, full_response=True)
    b = code(wf, "Build request", r"""
const s = $('Start').first().json;
// Approved and published posts are only changed through the approval form. A prompt
// injection read by another tool must not reschedule or pull them (audit M1).
const current = ($input.first().json.body || {}).status;
const base = $env.CALENDAR_URL;
const action = String(s.action || 'list').trim().toLowerCase();
// Only a calendar item number ("53", "#53", "item 53"). Hosted models have sent their own
// tool-call id ("fc_76a7...") here; stripping letters from it made a bogus id (night 5).
const idMatch = String(s.id || '').trim().match(/^(?:#|item\s*)?(\d{1,9})$/i);
const id = idMatch ? idMatch[1] : '';
const idHelp = 'an item number like 53 (use action list to find it)';
const refuse = msg => [{ json: { action, method: 'GET', url: `${base}/health`, body: '{}', blocked: msg } }];
if (['get', 'set_status', 'schedule'].includes(action) && !id) {
  return refuse(`${action} needs ${idHelp}; got "${String(s.id || '').slice(0, 40)}".`);
}
const need = (v, what) => { if (!v) throw new Error(`${action} needs ${what}`); return v; };
let req;
if (action === 'list') {
  // (URLSearchParams is not available in the Code node sandbox)
  const q = [['status', s.status], ['channel', s.channel]]
    .filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(String(v).trim())}`).join('&');
  req = { method: 'GET', url: `${base}/items${q ? '?' + q : ''}` };
} else if (action === 'get') {
  req = { method: 'GET', url: `${base}/items/${need(id, 'an id')}` };
} else if (action === 'create') {
  req = { method: 'POST', url: `${base}/items`, body: { title: need(s.title, 'a title'),
    channel: need(s.channel, 'a channel'), body: s.body || '', status: 'draft', scheduled_at: s.scheduled_at || null } };
} else if (['set_status', 'schedule'].includes(action) && ['approved', 'published'].includes(current)) {
  return refuse(`Item #${id} is ${current}; only a person can change it, in the approval form.`);
} else if (['set_status', 'schedule'].includes(action) && current === 'rejected') {
  // A reviewer's rejection stands: the agent revived a rejected post to schedule it (night 5).
  return refuse(`Item #${id} was rejected by a reviewer; only a person can reopen it. Suggest writing a new draft instead.`);
} else if (action === 'set_status') {
  // Enforced here, not in the tool description: the model must never approve or publish.
  const target = String(s.status || '').trim().toLowerCase();
  if (['approved', 'published'].includes(target)) {
    return refuse(`Only a person can approve or publish, in the approval form (${$env.N8N_PUBLIC_URL || ''}form/mkt-content-approval). I can move items to draft, in_review or rejected.`);
  }
  req = { method: 'POST', url: `${base}/items/${need(id, 'an id')}/status`, body: { status: need(s.status, 'a status'), note: 'via chat agent' } };
} else if (action === 'schedule') {
  req = { method: 'PATCH', url: `${base}/items/${need(id, 'an id')}`, body: { scheduled_at: need(s.scheduled_at, 'scheduled_at (ISO, UTC)') } };
} else {
  throw new Error(`Unknown action "${action}". Use list, get, create, set_status or schedule.`);
}
return [{ json: { action, method: req.method, url: req.url, body: JSON.stringify(req.body || {}), send: req.method !== 'GET' } }];
""")
    c = wf.add("Call calendar", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "={{ $json.method }}",
        "url": "={{ $json.url }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "X-API-Key", "value": "={{ $env.INTERNAL_API_KEY }}"}]},
        "sendBody": True,  # a boolean expression here is ignored by n8n; the calendar ignores GET bodies
        "specifyBody": "json",
        "jsonBody": "={{ $json.body }}",
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}}},
    })
    f = code(wf, "Format", r"""
const built = $('Build request').first().json;
if (built.blocked) return [{ json: { result: built.blocked } }];
const action = built.action;
const full = $input.first().json;               // full response: an empty list is still one item
const res = full.body;
if (full.statusCode >= 400 && !(res && res.detail)) return [{ json: { result: `Calendar error (HTTP ${full.statusCode}).` } }];
const rows = Array.isArray(res) ? res : [res];
const one = rows[0] || {};
if (one.detail) return [{ json: { result: `Calendar refused: ${typeof one.detail === 'string' ? one.detail : JSON.stringify(one.detail)}` } }];
const line = i => `#${i.id} [${i.status}] ${i.channel} · ${i.scheduled_at || 'unscheduled'} · ${i.title}`;
if (action === 'list') {
  const items = rows.filter(r => r && r.id);
  return [{ json: { result: items.length ? items.slice(0, 30).map(line).join('\n') : 'No matching calendar items.' } }];
}
return [{ json: { result: `${line(one)}\n\n${one.body || ''}`.trim() } }];
""")
    wf.chain(s, cur, b, c, f)
    return wf, {
        "summary": "Gives the agent access to the content calendar (19): list, get, create, change status and schedule items. Status changes follow the calendar's rules, so the agent can't skip human approval.",
        "inputs": {"action": "`list` · `get` · `create` · `set_status` · `schedule`", "id": "item id", "status": "filter (list) or new status (set_status)", "channel": "filter or new item channel", "title": "create", "body": "create", "scheduled_at": "ISO 8601 UTC for schedule/create"},
        "output": "`{result}`: readable lines",
        "depends": ["19-content-calendar"],
        "called_by": "the chat agent (24), tool `content_calendar`",
        "test": {"action": "list", "id": "", "status": "", "channel": "", "title": "", "body": "", "scheduled_at": ""},
    }

# --------------------------------------------------------------------------- 34 kb answer

def wf34():
    wf = Workflow(34, "Tool · Knowledge base answer")
    s = sub_trigger(wf, [("question", "string")])
    k = http(wf, "Search", "POST", "={{ $env.KB_URL }}/search", "={{ JSON.stringify({ query: $json.question, k: 5 }) }}")
    c = code(wf, "Excerpts", r"""
const hits = ($input.first().json.results || []).filter(r => r.score >= 0.3);
return [{ json: {
  found: hits.length > 0,
  question: $('Start').first().json.question,
  context: hits.map((r, i) => `[${i + 1}] (${r.title}) ${r.chunk}`).join('\n'),
  sources: hits.map((r, i) => `[${i + 1}] ${r.title}${r.source ? ' — ' + r.source : ''}`).join('\n'),
} }];
""")
    i = if_true(wf, "Anything found?", "={{ $json.found }}")
    g = gateway(wf, "Answer", "kb_answer", "{ question: $json.question, context: $json.context }", pos=[wf._x, 200])
    f = code(wf, "With sources", r"""
const answer = $input.first().json.output;
// The model answered "not in the knowledge base": listing sources would mislead.
if (/don't have that in the knowledge base/i.test(answer)) return [{ json: { result: answer } }];
return [{ json: { result: `${answer}\n\nSources:\n${$('Excerpts').first().json.sources}` } }];
""", pos=[wf._x + 240, 200])
    n = code(wf, "Nothing found", r"""
return [{ json: { result: "I don't have that in the knowledge base. Add it with the knowledge-base form (deploy 42)." } }];
""", pos=[wf._x, 420])
    wf.chain(s, k, c, i)
    wf.link(i, g, src_index=0)
    wf.link(i, n, src_index=1)
    wf.link(g, f)
    return wf, {
        "summary": "Answers questions about your brand only from the knowledge base (06), with numbered citations. If nothing relevant is found, it says so instead of guessing.",
        "inputs": {"question": "the question"},
        "output": "`{result}`: answer and sources",
        "depends": ["03-llm-gateway", "06-knowledge-base"],
        "called_by": "the chat agent (24), tool `ask_knowledge_base`",
        "test": {"question": "Can I pause my subscription?"},
    }

# --------------------------------------------------------------------------- 36 trend digest



def wf36():
    wf = Workflow(36, "Schedule · Morning trend digest")
    t = schedule(wf, "Every day 08:00", "0 8 * * *")
    r = http(wf, "Poll feeds", "POST", "={{ $env.RSS_URL }}/poll", "={{ JSON.stringify({ max_items_per_feed: 10 }) }}",
             key=True, continue_on_fail=True)
    m = http(wf, "Search mentions", "POST", "={{ $env.LISTENING_URL }}/search",
             "={{ JSON.stringify({ query: $env.LISTENING_QUERY || 'marketing', days: 1, limit: 10 }) }}",
             continue_on_fail=True)
    p = code(wf, "Prepare", r"""
const feeds = $('Poll feeds').first().json.new_items || [];
const mentions = $input.first().json.mentions || [];
if (!feeds.length && !mentions.length) return [];   // nothing new: stay quiet
const date = new Date().toISOString().slice(0, 10);
return [{ json: {
  date,
  items: feeds.slice(0, 25).map(i => `- ${i.title} (${i.feed}) ${i.link}`).join('\n') || '(no new articles)',
  mentions: mentions.slice(0, 10).map(m => `- [${m.source}] ${m.title} ${m.url}`).join('\n') || null,
} }];
""")
    g = gateway(wf, "Write digest", "trend_digest", "{ items: $json.items, mentions: $json.mentions, date: $json.date }")
    kb = http(wf, "Save to knowledge base", "POST", "={{ $env.KB_URL }}/docs",
              "={{ JSON.stringify({ doc_id: 'digest-' + $('Prepare').first().json.date, title: 'Trend digest ' + $('Prepare').first().json.date, text: $json.output, source: 'trend-digest' }) }}",
              key=True, continue_on_fail=True)
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "'*Morning digest*\\n' + $('Write digest').first().json.output")
    wf.chain(t, r, m, p, g, kb, gn, n)
    return wf, {
        "summary": "Every morning it polls your RSS feeds (08) and Hacker News/Reddit mentions (11), writes a short digest with post ideas, saves it to the knowledge base so the chat agent can answer \"what was in today's digest?\", and posts it to your webhook.",
        "schedule": "08:00 daily (n8n `GENERIC_TIMEZONE`)",
        "env": {"LISTENING_QUERY": "what to search for", "NOTIFY_WEBHOOK_URL": "Slack/Discord/Teams incoming webhook (optional)"},
        "depends": ["03-llm-gateway", "06-knowledge-base", "08-rss-watcher", "11-social-listening"],
    }

# --------------------------------------------------------------------------- 37 competitor watch

def wf37():
    # Pages (09) + ads and manual-check links from 78 + pricing briefs: workflows_radar.py.
    from workflows_radar import wf37 as build
    return build()

# --------------------------------------------------------------------------- 38 approval form

# What a decision does (approve, edit & approve, reject - rewrite, reject - drop, back to draft,
# skip) is shared by the approval form (38) and the control room's webhook (72-control-room/n8n),
# so both behave exactly alike. DECISIONS_CORE expects, defined before it: `a` (answers keyed
# like the form's fields: `Decision #<id>`, `Text #<id>`, `Reason #<id>`, `Publish at #<id>`,
# optional `Seen #<id>`; decisions are the form's option labels), `reviewer`, and `items`
# ({id: calendar item}) and VIDEO_SCRIPT_PARSE. It returns {ops, events, revisions, summary,
# videos} for decision_tail().
# Version-bound approval: `Seen #<id>` is the body_sha256 the reviewer saw (72 sends it from the
# card). Without it (the form 38, older control rooms) the item's body_sha256 from the fetch is
# used: for 38 that is the text the page was rendered with. Approve sends it to 19 as
# expected_sha256; edit & approve PATCHes with if_match_sha256 and approves with expected_body
# (the edited text). 19 answers 409 "changed since you looked" if the text changed meanwhile.
DECISIONS_CORE = r"""const cal = $env.CALENDAR_URL;
const nextHour = () => { const d = new Date(); d.setUTCMinutes(0, 0, 0); d.setUTCHours(d.getUTCHours() + 1); return d.toISOString(); };
const toIso = s => { s = String(s || '').trim(); if (!s) return null;
  const d = new Date(s.replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? '' : 'Z')); return isNaN(d) ? null : d.toISOString(); };
const norm = s => String(s || '').replace(/\r\n/g, '\n').trim();
const hex64 = s => /^[0-9a-f]{64}$/.test(String(s || '')) ? String(s) : null;
const ops = [], events = [], revisions = [], summary = [], videos = [];
for (const [id, it] of Object.entries(items)) {
  const choice = a[`Decision #${id}`];
  if (!choice || choice === 'Skip') continue;
  const edited = norm(a[`Text #${id}`]);
  const reason = norm(a[`Reason #${id}`]);
  const when = toIso(a[`Publish at #${id}`]) || it.scheduled_at || nextHour();
  const seen = hex64(a[`Seen #${id}`]) || hex64(it.body_sha256);
  const ifMatch = seen ? { if_match_sha256: seen } : {};
  const note = s => `${s} by ${reviewer}${reason ? ': ' + reason : ''}`;
  const event = (decision, final) => ({ item_id: Number(id), channel: it.channel, campaign_id: it.campaign_id ?? null,
    decision, draft: it.body, final: final ?? null, reason: reason || null, reviewer });
  const ev = (decision, final) => events.push(event(decision, final));
  if (choice === 'Approve' || choice === 'Edit & approve') {
    const changed = choice === 'Edit & approve' && edited && edited !== norm(it.body);
    const patch = { scheduled_at: when, ...ifMatch };
    if (changed) patch.body = edited;
    // What the reviewer approved: the text they saw, or the text they wrote.
    const bound = changed ? { expected_body: edited } : seen ? { expected_sha256: seen } : {};
    // An edited video script: its video (71, rendered from the old script) no longer matches.
    // It is rendered again from the edited text before approving ('Final decisions').
    const hasVideo = /^https?:\/\//.test(it.video_url || '');
    // A clip cut from the user's own video (73, tool 77): the edited text is its caption, the
    // video does not depend on it, so it is approved as it is.
    const isClip = /\/clips\/[0-9a-f]{32}\.mp4$/.test(it.video_url || '');
    if (changed && !isClip && (hasVideo || (String(it.channel || '').toLowerCase() === 'video' && $env.VIDEO_URL))) {
      videos.push({ id, ...parseVideoScript(edited, it.body), patch, bound, when, note: note('edited and approved'),
        event: event('edited', edited), old_video: it.video_url || null, old_image: it.image_url || null });
      continue;
    }
    ops.push({ stage: 1, method: 'PATCH', url: `${cal}/items/${id}`, body: patch });
    ops.push({ stage: 2, method: 'POST', url: `${cal}/items/${id}/status`, body: { status: 'approved', note: note(changed ? 'edited and approved' : 'approved'), ...bound } });
    ev(changed ? 'edited' : 'approved', changed ? edited : it.body);
    summary.push(`#${id}: ${changed ? 'edited and approved' : 'approved'} for ${when.slice(0, 16).replace('T', ' ')} UTC`);
  } else if (choice.startsWith('Reject')) {
    const rewrite = choice.includes('rewrite');
    ops.push({ stage: 1, method: 'POST', url: `${cal}/items/${id}/status`, body: { status: 'rejected', note: note('rejected') } });
    ev('rejected', null);
    if (rewrite && reason) {
      ops.push({ stage: 2, method: 'POST', url: `${cal}/items/${id}/status`, body: { status: 'draft', note: 'sent back for an automatic rewrite' } });
      revisions.push({ item_id: String(id), reason });
      summary.push(`#${id}: rejected, being rewritten ("${reason}")`);
    } else {
      summary.push(`#${id}: rejected${rewrite ? ' (no reason given, so it was not rewritten)' : ''}`);
    }
  } else if (choice === 'Back to draft') {
    ops.push({ stage: 1, method: 'POST', url: `${cal}/items/${id}/status`, body: { status: 'draft', note: note('sent back to draft') } });
    summary.push(`#${id}: back to draft`);
  }
}
return [{ json: { ops, events, revisions, summary, videos } }];
"""


# 19 refuses to approve (or edit) an item whose text changed after the reviewer looked: 409
# {"message": "changed since you looked"}, or 428 for a bound item sent without a hash. Those
# items come back as one clear line each instead of raw JSON, and are left out of the learning
# and engine events (nothing was approved). `stages` is [[Stage k items, Run stage k items], ...]:
# run node k's results pair index-by-index with the ops Code node k sent.
STALE_JS = r"""
const STALE_LINE = id => `#${id}: NOT approved: changed since you looked — reopen the card`;
function stageResults(stages) {
  const out = [];
  for (const [sent, res] of stages) res.forEach((r, i) => {
    const j = (r || {}).json || {}, op = ((sent[i] || {}).json) || {};
    if (j.noop || op.noop || !(j.statusCode >= 300)) return;
    const det = (j.body || {}).detail ?? j.body ?? null;
    const m = String(op.url || '').match(/\/items\/(\d+)/);
    const stale = j.statusCode === 428 || (j.statusCode === 409 && det && typeof det === 'object' && det.message === 'changed since you looked');
    out.push({ item_id: m ? Number(m[1]) : null, status: j.statusCode, detail: det, stale });
  });
  return out;
}
function staleIds(failures) { return [...new Set(failures.filter(f => f.stale && f.item_id).map(f => f.item_id))]; }
const stageFailures = () => stageResults([1, 2].map(k => [$(`Stage ${k}`).all(), $(`Run stage ${k}`).all()]));
"""


def decision_tail(wf, items_node):
    """Every node after 'Decisions': video re-render (71), calendar ops in two stages with the
    approver key (19), learning events (46), engine outcomes (61), rewrites (49) and 'Summary'
    ({message}). `items_node` is the node whose first item holds {items: [...]}. Returns the
    node names in chain order."""
    from n8nlib import OPS_TO_ITEMS, dynamic_http, staged_ops
    wf._x += 480
    bp = brand_profile(wf)
    wf.nodes[-1]["position"] = [wf._x - 240, 200]
    vq = code(wf, "Video request", VIDEO_REQUEST + r"""
// One render per edited video script that parsed; the others only pass through (GET /health).
const v = $('Decisions').first().json.videos;
const pass = { skip: true, method: 'GET', url: `${$env.VIDEO_URL || $env.CALENDAR_URL}/health`, body: '{}' };
return v.length ? v.map(x => ({ json: { id: x.id, ...(x.script ? videoRequest(x.script) : pass) } })) : [{ json: { id: null, ...pass } }];
""", pos=[wf._x, 200])
    vr = video_render(wf, pos=[wf._x + 240, 200])
    fd = code(wf, "Final decisions", VIDEO_RESULT + r"""
// An edited video item is approved only with a video of the edited script: the new video_url
// is PATCHed in stage 1 while the item is still in_review (approved items are frozen), then
// stage 2 approves it. If the script can't be read or 71 can't render it, the stale video is
// cleared and the item goes back to draft: it is never approved with the old video.
const d = $('Decisions').first().json;
const reqs = $('Video request').all().map(i => i.json);
const res = $('Render video').all().map(i => i.json);
const ops = [...d.ops], events = [...d.events], summary = [...d.summary];
for (const v of d.videos) {
  const k = reqs.findIndex(r => r.id === v.id);
  const req = reqs[k] || { skip: true };
  const r = v.error ? null : videoResult(req, res[k]);
  const url = `${$env.CALENDAR_URL}/items/${v.id}`;
  // The poster of the old video (57/65 use it as the image) is replaced or cleared with it.
  const oldPoster = v.old_video ? v.old_video.replace(/\.mp4$/, '.jpg') : null;
  const posterIsImage = !v.old_image || v.old_image === oldPoster;
  if (r && r.video_url) {
    const patch = { ...v.patch, video_url: r.video_url, ...(posterIsImage && r.poster_url ? { image_url: r.poster_url } : {}) };
    ops.push({ stage: 1, method: 'PATCH', url, body: patch });
    ops.push({ stage: 2, method: 'POST', url: `${url}/status`, body: { status: 'approved',
      note: `${v.note} | video re-rendered after edit, ${r.video_note}`.slice(0, 1500), ...(v.bound || {}) } });
    events.push(v.event);
    summary.push(`#${v.id}: edited and approved for ${v.when.slice(0, 16).replace('T', ' ')} UTC; video re-rendered after edit (${r.video_note.replace(/^.*\(/, '').replace(/\)$/, '')})`);
  } else {
    const why = v.error ? `the script could not be read: ${v.error}`
      : r && r.video_note ? r.video_note.replace(/^video not rendered: /, '')
      : 'VIDEO_URL is not set';
    const patch = { body: v.patch.body, video_url: null, ...(v.old_image && v.old_image === oldPoster ? { image_url: null } : {}),
      ...(v.patch.if_match_sha256 ? { if_match_sha256: v.patch.if_match_sha256 } : {}) };
    ops.push({ stage: 1, method: 'PATCH', url, body: patch });
    ops.push({ stage: 2, method: 'POST', url: `${url}/status`, body: { status: 'draft',
      note: `script edited: video could not be re-rendered (${why}), render it again before approving`.slice(0, 1500) } });
    summary.push(`#${v.id}: NOT approved, back to draft: your edit is saved, but the video could not be re-rendered (${why}); the old video was removed. Render it again before approving.`);
  }
}
return [{ json: { ops, events, summary } }];
""", pos=[wf._x + 480, 200])
    wf._x += 240
    ol = code(wf, "Calendar ops", r"""
const ops = $('Final decisions').first().json.ops;
return ops.length ? ops.map(o => ({ json: o })) : [{ json: { stage: 0 } }];
""", pos=[wf._x + 480, 200])
    st = staged_ops(wf, "Calendar ops", 2, "$env.CALENDAR_URL", approver=True)  # the person approves here
    el = code(wf, "Learning events", OPS_TO_ITEMS + STALE_JS + r"""
const stale = staleIds(stageFailures());
const events = $('Final decisions').first().json.events.filter(e => !stale.includes(e.item_id))
  .map(e => ({ method: 'POST', url: `${$env.LEARNING_URL}/events`, body: e }));
return opsToItems(events, $env.LEARNING_URL);
""", pos=[wf._x + 960, 200])
    re_ = dynamic_http(wf, "Log decisions", pos=[wf._x + 1200, 200])
    # Content-engine items (notes "engine pillar #P", from 64) report each decision to 61's
    # stop rule. Fail-soft: without ENGINE_URL, or if 61 is down, the form still finishes.
    eo = code(wf, "Engine outcomes", OPS_TO_ITEMS + STALE_JS + r"""
const items = $('Build review page').first().json.items;
const stale = staleIds(stageFailures());
const ops = [];
for (const e of $('Final decisions').first().json.events.filter(x => !stale.includes(x.item_id))) {
  const it = items.find(i => Number(i.id) === e.item_id) || {};
  const m = String(it.notes || '').match(/engine pillar #(\d+)/);
  if (m) ops.push({ method: 'POST', url: `${$env.ENGINE_URL}/pillars/${m[1]}/outcomes`, body: { item_id: e.item_id, decision: e.decision } });
}
return opsToItems($env.ENGINE_URL ? ops : [], $env.LEARNING_URL);
""", pos=[wf._x + 1200, 400])
    # The node that holds the reviewed items ({items: [...]}) differs per workflow.
    wf.nodes[-1]["parameters"]["jsCode"] = wf.nodes[-1]["parameters"]["jsCode"].replace(
        "$('Build review page')", f"$('{items_node}')")
    eh = dynamic_http(wf, "Record engine outcomes", pos=[wf._x + 1440, 400], timeout=15000)
    wf.nodes[-1]["onError"] = "continueRegularOutput"
    rl = code(wf, "Rewrites", r"""
const r = $('Decisions').first().json.revisions;
return r.length ? r.map(x => ({ json: x })) : [{ json: { item_id: '', reason: '' } }];
""", pos=[wf._x + 1440, 200])
    rv = call_workflow(wf, "Start rewrites", 49, {"item_id": "={{ $json.item_id }}", "reason": "={{ $json.reason }}"},
                       wait=False, pos=[wf._x + 1680, 200])
    sm = code(wf, "Summary", STALE_JS + r"""
// {message} for the form; {summary, stale, failed} per item for the control room (72).
const d = $('Final decisions').first().json;
const failures = stageFailures();
const stale = staleIds(failures);
const summary = d.summary.filter(l => !stale.some(id => l.startsWith(`#${id}:`))).concat(stale.map(STALE_LINE));
const failed = failures.filter(f => !f.stale).map(({ item_id, status, detail }) => ({ item_id, status, detail }));
const lines = summary.length ? [...summary] : ['No decisions made.'];
if (failed.length) lines.push('', 'Some updates failed:', ...failed.map(f => JSON.stringify(f.detail)));
return [{ json: { message: lines.join('\n'), summary, stale, failed } }];
""", pos=[wf._x + 1920, 200])
    return [bp, vq, vr, fd, ol, *st, el, re_, eo, eh, rl, rv, sm]


def wf38():
    wf = Workflow(38, "Form · Content approval")
    t = wf.add("Open approval form", "n8n-nodes-base.formTrigger", 2.2, {
        "authentication": "basicAuth",
        "formTitle": "Content approval",
        "formDescription": "Review drafts the agent wrote. Nothing is published until you approve it here. Your edits and reasons teach the agent.",
        "formFields": {"values": [{"fieldLabel": "Reviewer", "placeholder": "your name", "requiredField": True}]},
        "responseMode": "lastNode",
        "options": {},
    }, webhookId="mkt-content-approval", credentials=FORMS_CRED)
    l = http(wf, "Items in review", "GET", "={{ $env.CALENDAR_URL }}/items", key=True,
             query={"status": "in_review"}, full_response=True)
    b = code(wf, "Build review page", r"""
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const items = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(i => i && i.id).slice(0, 8);
const fields = [];
for (const it of items) {
  fields.push({ fieldType: 'html', html:
    `<h3>#${it.id} · ${esc(it.channel)} · ${esc(it.title)}</h3>` +
    // The image (a card from 17) loads from CARDS_PUBLIC_URL, so it must be reachable from your browser.
    // n8n's form sanitizer drops style attributes on <img>; width (height follows) is kept.
    (/^https?:\/\//.test(it.image_url || '') ? `<p><img src="${esc(it.image_url)}" alt="image for #${it.id}" width="320"></p>` : '') +
    // A video preview (71) loads from its PUBLIC_BASE_URL; the length comes from the note 57/65 wrote.
    (/^https?:\/\//.test(it.video_url || '') ? (() => {
      const m = String(it.notes || '').split(`video preview: ${it.video_url} (`)[1];
      const secs = m && /^[\d.]+ s\)/.test(m) ? ` (${Math.round(parseFloat(m))} s)` : '';
      const poster = /^https?:\/\//.test(it.image_url || '') ? '' : it.video_url.replace(/\/videos\/([0-9a-f]{32})\.mp4$/, '/videos/$1.jpg');
      return `<p><a href="${esc(it.video_url)}" target="_blank">▶ Watch the video${secs}</a></p>` +
        (poster && poster !== it.video_url ? `<p><a href="${esc(it.video_url)}" target="_blank"><img src="${esc(poster)}" alt="video poster for #${it.id}" width="320"></a></p>` : '');
    })() : '') +
    (it.notes ? `<p style="color:#888;font-size:12px">${esc(String(it.notes).slice(-300))}</p>` : '') });
  fields.push({ fieldLabel: `Decision #${it.id}`, fieldType: 'dropdown', requiredField: true,
    fieldOptions: { values: ['Skip', 'Approve', 'Edit & approve', 'Reject – rewrite it', 'Reject – drop it', 'Back to draft'].map(option => ({ option })) } });
  fields.push({ fieldLabel: `Text #${it.id}`, fieldType: 'textarea', defaultValue: String(it.body || '') });
  fields.push({ fieldLabel: `Reason #${it.id}`, fieldType: 'text',
    placeholder: 'why? (required to rewrite; teaches the agent)' });
  fields.push({ fieldLabel: `Publish at #${it.id}`, fieldType: 'text',
    placeholder: it.scheduled_at || 'YYYY-MM-DD HH:MM (UTC) — blank = next full hour' });
}
return [{ json: { count: items.length, fields, items } }];
""")
    i = if_true(wf, "Anything to review?", "={{ $json.count > 0 }}")
    pg = wf.add("Review page", "n8n-nodes-base.form", 2.3, {
        "operation": "page",
        "defineForm": "json",
        "jsonOutput": "={{ JSON.stringify($json.fields) }}",
        "options": {"formTitle": "Content approval", "buttonLabel": "Save decisions"},
    }, pos=[wf._x, 200])
    d = code(wf, "Decisions", VIDEO_SCRIPT_PARSE + r"""
// A field sent twice arrives as a list; the last value wins.
const a = Object.fromEntries(Object.entries($input.first().json).map(([k, v]) => [k, Array.isArray(v) ? v[v.length - 1] : v]));
const reviewer = $('Open approval form').first().json.Reviewer || 'reviewer';
const items = Object.fromEntries($('Build review page').first().json.items.map(i => [String(i.id), i]));
""" + DECISIONS_CORE, pos=[wf._x + 240, 200])
    tail = decision_tail(wf, "Build review page")
    done = wf.add("Done", "n8n-nodes-base.form", 2.3, {
        "operation": "completion", "respondWith": "text",
        "completionTitle": "Saved", "completionMessage": "={{ $json.message }}", "options": {},
    }, pos=[wf._x + 2160, 200])
    empty = wf.add("Nothing to review", "n8n-nodes-base.form", 2.3, {
        "operation": "completion", "respondWith": "text",
        "completionTitle": "All clear", "completionMessage": "No drafts are waiting for review.", "options": {},
    }, pos=[wf._x, 420])
    wf.chain(t, l, b, i)
    wf.link(i, pg, src_index=0)
    wf.link(i, empty, src_index=1)
    wf.chain(pg, d, *tail, done)
    return wf, {
        "summary": "A web form where a person reviews drafts that are `in_review`. For each one: approve, edit the text and approve, reject with a reason (it is rewritten automatically by 49, up to 3 times), reject and drop, or send it back to draft, plus when it should publish. Every decision and edit is logged in the learning service (46), which is how the agent learns your preferences. It's the only way content reaches `approved`, and the publisher (39) only publishes approved items. A draft with an `image_url` (a card from 17) shows the image; it loads from `CARDS_PUBLIC_URL` (default `http://localhost:8117`), so that address must be reachable from the reviewer's browser. A draft with a `video_url` (a preview from 71, for video scripts) shows a link to watch it and its poster; they load from 71's `PUBLIC_BASE_URL`. When a video script is edited and approved, the video no longer matches the text, so the form reads the edited script back into the `video_script` JSON (hook, beats, close, caption, hashtags; each beat keeps its shot from the old shot list; the `Estimated length` and `Shot list` lines are ignored) and 71 renders it again (`VIDEO_URL`, voice `VIDEO_VOICE`, brand from 05). The new `video_url` (and poster, if the poster was the image) is saved while the item is still `in_review`, then it is approved with the note `video re-rendered after edit`. If the edited script can't be read (no hook, no beats, more than 8 beats, no close, …) or 71 can't render it, the item is **not** approved: the edit is saved, the old video is removed, the item goes back to `draft` with the note `script edited: video could not be re-rendered (<reason>), render it again before approving`, and the form's summary says so. Each approval is bound to the text on the page: the form sends 19 the `body_sha256` of each draft as it was when the page was rendered (`expected_sha256` for approve; `if_match_sha256` on the edit and `expected_body` = your text for edit & approve). If the draft changed after the page was opened (a rewrite, an edit in the control room), 19 changes nothing and the summary says `#N: NOT approved: changed since you looked — reopen the card`; the other decisions go through.",
        "trigger": "n8n form at `<N8N_PUBLIC_URL>/form/mkt-content-approval`",
        "depends": ["19-content-calendar", "46-learning-service", "49-wf-revise-draft"],
    }

# --------------------------------------------------------------------------- 39 publisher

def wf39():
    wf = Workflow(39, "Schedule · Publisher")
    t = schedule(wf, "Every 15 minutes", "*/15 * * * *")
    due = http(wf, "Due items", "GET", "={{ $env.CALENDAR_URL }}/due", key=True, full_response=True)
    sp = code(wf, "One per item", r"""
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const all = raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw.filter(i => i && i.id);
// Router: blog -> CMS_PUBLISH_URL (62 cms-bridge), every other channel -> PUBLISH_WEBHOOK_URL.
// A channel whose URL is empty is skipped: nothing sent, nothing marked.
// Review replies (60) are never sent anywhere: a person copies the approved reply to
// Google or Trustpilot by hand. Newsletters (66) already sit in Listmonk as a draft campaign.
// A blog post the CMS kept as a draft stays approved with the note "sent to CMS as ..."
// (written below) and is never sent again. A DRY-RUN send only holds it for 24 h: after the
// bridge goes live the post is sent for real within a day, with nothing to clean up by hand.
const isBlog = i => String(i.channel).toLowerCase() === 'blog';
const cmsHeld = i => {
  const lines = String(i.notes || '').split('\n').filter(l => /sent to CMS as /.test(l));
  if (lines.some(l => !/\(dry run/.test(l))) return true;                 // really sent: done
  const last = lines.map(l => Date.parse((l.match(/^\[([^\]]+)\]/) || [])[1] || '')).filter(n => !isNaN(n));
  return last.length > 0 && Date.now() - Math.max(...last) < 24 * 3600 * 1000;
};
// Work items, not posts: a refresh plan (68) and an SEO brief (69) are instructions for a person,
// never content to publish, even once approved.
// A client report (85) is forwarded to the client by a person, never sent from here.
// An email flow (86) is a whole sequence: approving it lets 86 run it, it is never posted.
const NOT_POSTS = ['review_reply', 'newsletter', 'blog_refresh', 'seo_brief', 'competitor_brief', 'lead_reply', 'visibility_gap', 'positioning', 'client_report', 'email_flow'];
// Social posts sent while the publish bridge is in DRY_RUN get the note "publish dry run" and are
// held 24 h like blog dry runs; otherwise they were re-sent every 15 minutes, each time with a new
// short link (~96 a day per approved post, Docker run on 2026-09-28).
const socialHeld = i => {
  const last = String(i.notes || '').split('\n').filter(l => /publish dry run/.test(l))
    .map(l => Date.parse((l.match(/^\[([^\]]+)\]/) || [])[1] || '')).filter(n => !isNaN(n));
  return last.length > 0 && Date.now() - Math.max(...last) < 24 * 3600 * 1000;
};
const items = all.filter(i => !NOT_POSTS.includes(String(i.channel).toLowerCase()))
  .filter(i => isBlog(i) ? !!$env.CMS_PUBLISH_URL && !cmsHeld(i) : !!$env.PUBLISH_WEBHOOK_URL && !socialHeld(i));
// The stored link is the base URL; the text usually carries it with UTM parameters.
// Shorten the full URL as it appears in the text so tracking survives.
return items.map(i => {
  // Campaign drafts carry their tracked URL only in the text (link field empty): use the
  // first URL then, or campaign clicks could never be counted through the short link.
  const urls = (String(i.body).match(/https?:\/\/\S+/g) || []).map(u => u.replace(/[).,!?]+$/, ''));
  const inText = i.link ? urls.find(u => u.startsWith(i.link)) : urls[0];
  const full = inText || i.link || null;
  // Fill in missing UTM tags (a reviewer may paste a plain link): utm_content = calendar item
  // id so clicks map back to this exact post; campaign posts also get their campaign tags,
  // or the campaign's click count would miss them.
  const MEDIUM = { email: 'email', blog: 'referral', google_ads: 'cpc' };
  const add = (u, k, v) => !u || !v || new RegExp(`[?&]${k}=`).test(u) ? u : `${u}${u.includes('?') ? '&' : '?'}${k}=${encodeURIComponent(v)}`;
  let tracked = full;
  if (i.campaign) {
    tracked = add(tracked, 'utm_source', String(i.channel).toLowerCase());
    tracked = add(tracked, 'utm_medium', MEDIUM[i.channel] || 'social');
    tracked = add(tracked, 'utm_campaign', i.campaign);
  }
  tracked = add(tracked, 'utm_content', String(i.id));
  return { json: { ...i, full_link: full, tracked_link: tracked } };
});
""")
    sh = http(wf, "Short link", "POST", "={{ $env.SHORTENER_URL }}/links",
              "={{ JSON.stringify({ url: $json.tracked_link || 'none' }) }}", key=True, never_error=True)
    pl = code(wf, "Payload", r"""
const item = $('One per item').item.json;
const short = $json.short_url;
const text = item.full_link && short ? String(item.body).split(item.full_link).join(short) : item.body;
return { json: { id: item.id, channel: item.channel, title: item.title, text, link: short || item.full_link || null,
  short_url: short || null, campaign: item.campaign, image_url: item.image_url || null,
  video_url: item.video_url || null, scheduled_at: item.scheduled_at || null } };
""", each=True)
    blog = "String($json.channel).toLowerCase() === 'blog'"
    pb = wf.add("Publish", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": "={{ %s ? $env.CMS_PUBLISH_URL : $env.PUBLISH_WEBHOOK_URL }}" % blog,
        # Blog -> cms-bridge (62) with CMS_PUBLISH_KEY (or INTERNAL_API_KEY). Social: the key is
        # only sent when PUBLISH_WEBHOOK_KEY is set (e.g. for 54-postiz-bridge); an outside
        # webhook (Zapier...) never receives the internal key.
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "X-API-Key", "value":
            "={{ %s ? ($env.CMS_PUBLISH_KEY || $env.INTERNAL_API_KEY) : ($env.PUBLISH_WEBHOOK_KEY || '') }}" % blog}]},
        "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json) }}",
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}}, "timeout": 30000},
    })
    ok = code(wf, "Succeeded", r"""
// Only items the publish endpoint accepted are marked published; failures stay approved
// and are retried on the next run.
const LIVE = ['publish', 'future', 'published', 'scheduled'];   // CMS statuses that go live (62)
const out = [];
$input.all().forEach((r, i) => {
  const item = $('Payload').all()[i].json;
  const body = r.json.body && typeof r.json.body === 'object' ? r.json.body : {};
  // A dry run (54-postiz-bridge / 62-cms-bridge with DRY_RUN=true) answers 200 but published nothing.
  const dry = body.status === 'dry_run';
  // A CMS draft is not published: a person presses Publish in the CMS (see "CMS drafts").
  const cmsDraft = String(item.channel).toLowerCase() === 'blog' && !LIVE.includes(String(body.cms_status || '').toLowerCase());
  if (r.json.statusCode >= 200 && r.json.statusCode < 300 && !dry && !cmsDraft) {
    out.push({ json: { id: item.id, external_url: body.external_url || body.url || null, short_url: item.short_url } });
  }
});
return out;
""")
    mk = http(wf, "Mark published", "POST", "={{ $env.CALENDAR_URL }}/items/{{ $json.id }}/published",
              "={{ JSON.stringify({ external_url: $json.external_url, short_url: $json.short_url }) }}", key=True,
              approver=True)
    cd = code(wf, "CMS drafts", r"""
// Blog posts the CMS accepted as a draft (or a cms-bridge dry run): not published. The item
// stays approved and gets the note "sent to CMS as <status>: <url>", which "One per item"
// skips, so it is not sent again. To send it again, remove that line from the notes.
const LIVE = ['publish', 'future', 'published', 'scheduled'];
const out = [];
$input.all().forEach((r, i) => {
  const item = $('Payload').all()[i].json;
  const body = r.json.body && typeof r.json.body === 'object' ? r.json.body : {};
  if (!(r.json.statusCode >= 200 && r.json.statusCode < 300)) return;   // failed: retried next run
  if (String(item.channel).toLowerCase() !== 'blog') {
    // Social dry run: note it, so "One per item" holds the post for 24 h (see socialHeld).
    if (body.status === 'dry_run') out.push({ json: { id: item.id, note: 'publish dry run (bridge DRY_RUN=true, nothing posted)', external_url: null } });
    return;
  }
  const st = String(body.cms_status || 'draft').toLowerCase();
  const url = body.external_url || body.url || null;
  if (body.status === 'dry_run') {
    out.push({ json: { id: item.id, note: `sent to CMS as ${st} (dry run: cms-bridge DRY_RUN=true, nothing created)`, external_url: null } });
  } else if (!LIVE.includes(st)) {
    out.push({ json: { id: item.id, note: `sent to CMS as ${st}: ${url || '(no URL returned)'}`, external_url: url } });
  }
});
return out;
""", pos=[wf._x - 240, 480])
    nt = http(wf, "Add note", "POST", "={{ $env.CALENDAR_URL }}/items/{{ $json.id }}/notes",
              "={{ JSON.stringify({ note: $json.note, external_url: $json.external_url }) }}", key=True,
              pos=[wf._x, 480])
    wf.chain(t, due, sp, sh, pl, pb, ok, mk)
    wf.chain(pb, cd, nt)
    return wf, {
        "summary": "Every 15 minutes it takes the approved calendar items that are due, swaps each link for a tracked short link (16) and routes each post by channel: `blog` goes to the CMS bridge (`CMS_PUBLISH_URL`, 62), every other channel to your publish endpoint (`PUBLISH_WEBHOOK_URL`, e.g. 54 Postiz). A channel whose URL is empty is skipped. An item is marked published only if the endpoint accepted it and it really went live: a dry run is not published, and a blog post the CMS created as a **draft** stays `approved` with the note `sent to CMS as draft: <url>` (and `external_url` set), so a person presses Publish in the CMS and the post is not sent again. A CMS that publishes live (`cms_status` `publish`/`future`/`published`/`scheduled`) marks it published. Review replies (channel `review_reply`, from 60) and newsletters (`newsletter`, 66, already a Listmonk draft) are never sent: a person handles those by hand. Nor are work items for people (`blog_refresh`, `seo_brief`, `competitor_brief`, `lead_reply`, `visibility_gap`, `positioning`) or client reports (`client_report`, 85): a person forwards an approved report. An approved email flow (`email_flow`, 86) is never posted either: approving it lets 86 run the sequence.",
        "schedule": "every 15 minutes",
        "env": {"PUBLISH_WEBHOOK_URL": "endpoint for every channel except `blog`; it receives `{id, channel, title, text, link, short_url, campaign, image_url, video_url, scheduled_at}` (`image_url`: the post's image, e.g. a card from 17, or null; `video_url`: its video, e.g. an MP4 from 71, or null): a Zapier/Make/Buffer hook or your own, e.g. 54-postiz-bridge. Empty = social items are not sent.",
                "PUBLISH_WEBHOOK_KEY": "optional; sent as `X-API-Key` to `PUBLISH_WEBHOOK_URL` (only when set)",
                "CMS_PUBLISH_URL": "endpoint for `blog` items, same payload: `http://cms-bridge:8000/publish` (62). Empty = blog items are not sent.",
                "CMS_PUBLISH_KEY": "sent as `X-API-Key` to `CMS_PUBLISH_URL`; empty = `INTERNAL_API_KEY`"},
        "setup": "To post straight from n8n instead, replace the **Publish** node with n8n's LinkedIn, X or Facebook Graph node (with its credential) and keep the **Succeeded** check after it.\n\nA blog item that was sent to the CMS carries the note `sent to CMS as ...` and is skipped from then on. A dry-run send (cms-bridge `DRY_RUN=true`) only holds the item for 24 hours, so after going live every approved blog item is sent for real within a day; nothing to delete by hand.",
        "depends": ["16-link-shortener", "19-content-calendar", "62-cms-bridge (blog items, optional)",
                    "54-postiz-bridge or any webhook (social items, optional)"],
    }

# --------------------------------------------------------------------------- 40 content planner

def wf40():
    wf = Workflow(40, "Schedule · Weekly content planner")
    t = schedule(wf, "Mondays 07:00", "0 7 * * 1")
    perf = http(wf, "What performed", "GET", "={{ $env.CAMPAIGNS_URL }}/insights", query={"days": "60"},
                continue_on_fail=True)
    r = http(wf, "Recent posts", "GET", "={{ $env.CALENDAR_URL }}/items", key=True, query={"status": "published"},
             continue_on_fail=True, full_response=True)
    p = code(wf, "Prepare", r"""
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const recent = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(i => i && i.title).slice(-20);
const d = new Date(); d.setUTCHours(0, 0, 0, 0);
d.setUTCDate(d.getUTCDate() + ((8 - d.getUTCDay()) % 7 || 7));   // next Monday
const channels = ($env.PLAN_CHANNELS || 'linkedin, instagram, x').split(',').map(c => c.trim().toLowerCase()).filter(Boolean);
const ins = $('What performed').first().json;
const perf = [
  ...(ins.by_channel || []).map(c => `- ${c.channel}: ${c.avg_clicks} clicks per post (${c.posts} posts)`),
  ...(ins.top_posts || []).slice(0, 5).map(p => `- top: ${p.channel} "${p.title}" (${p.clicks} clicks)`),
].join('\n');
return [{ json: { week_start: d.toISOString().slice(0, 10), channels, performance: perf || null,
  recent_titles: recent.map(i => `- ${i.title}`).join('\n') || null } }];
""")
    g = gateway(wf, "Plan week", "content_plan",
                "{ week_start: $json.week_start, channels: $json.channels.join(', '), posts_per_channel: $env.PLAN_POSTS_PER_CHANNEL || 3, recent_titles: $json.recent_titles, themes: $env.PLAN_THEMES || null, performance: $json.performance }")
    # The content engine (64) may already fill days of that week: its items carry the note
    # "engine pillar #P". No planner idea goes on a channel and day that has one.
    ew = http(wf, "Engine items", "GET", "={{ $env.CALENDAR_URL }}/items", key=True, full_response=True,
              continue_on_fail=True, query={"from": "={{ $('Prepare').first().json.week_start }}",
                                            "to": "={{ new Date(Date.parse($('Prepare').first().json.week_start) + 6 * 864e5).toISOString().slice(0, 10) }}"})
    it = code(wf, "To calendar items", r"""
const p = $('Prepare').first().json;
const days = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
const raw = [].concat(($input.first().json || {}).body ?? []);
const engine = new Set((raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw)
  .filter(i => i && /engine pillar #\d+/.test(String(i.notes || '')) && i.scheduled_at)
  .map(i => `${String(i.channel).toLowerCase()}|${String(i.scheduled_at).slice(0, 10)}`));
return $('Plan week').first().json.output.items
  .filter(i => p.channels.includes(String(i.channel).trim().toLowerCase()))
  .filter(i => !engine.has(`${String(i.channel).trim().toLowerCase()}|${new Date(Date.parse(p.week_start) + (days[i.day] ?? 0) * 864e5).toISOString().slice(0, 10)}`))
  .map(i => {
    const d = new Date(p.week_start + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + days[i.day]);
    const [h, m] = i.time.split(':').map(Number);
    d.setUTCHours(Math.min(h, 23), m, 0, 0);
    return { json: { title: i.title, channel: i.channel.trim().toLowerCase(), status: 'idea',
      body: `[${i.angle}] ${i.brief}`, scheduled_at: d.toISOString(), campaign: `week-${p.week_start}` } };
  });
""")
    sv = http(wf, "Add ideas", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json) }}", key=True)
    sm = code(wf, "Summary", r"""
const lines = $input.all().map(i => `- ${i.json.scheduled_at.slice(0, 16).replace('T', ' ')} ${i.json.channel}: ${i.json.title} (#${i.json.id})`);
return [{ json: { text: `*Content plan for week of ${$('Prepare').first().json.week_start}* — ${lines.length} ideas in the calendar\n${lines.join('\n')}` } }];
""")
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, perf, r, p, g, ew, it, sv, sm, gn, n)
    return wf, {
        "summary": "Every Monday it plans the following week, using which channels and posts got the most clicks recently (45 insights): a mix of formats for each channel, avoiding angles published recently. The plan goes into the calendar as `idea` items with dates. Ask the chat agent to write any of them. A channel and day that already has a content-engine item (64, note `engine pillar #P`) gets no planner idea, so the two don't stack.",
        "schedule": "Mondays 07:00",
        "env": {"PLAN_CHANNELS": "default `linkedin, instagram, x`", "PLAN_POSTS_PER_CHANNEL": "default 3", "PLAN_THEMES": "optional", "NOTIFY_WEBHOOK_URL": "optional"},
        "depends": ["03-llm-gateway", "19-content-calendar"],
    }

# --------------------------------------------------------------------------- 41 weekly report

from workflows_p5 import WEEKLY_ACTION_DATA, WEEKLY_CHECK_ACTIONS  # noqa: E402  (41 next actions)
from workflows_p8 import WEEKLY_EXPERIMENTS  # noqa: E402  (41 experiments section)
from workflows_radar import WEEKLY_SUGGESTIONS  # noqa: E402  (41 competitor suggestions)
from workflows_p10 import WEEKLY_VISIBILITY  # noqa: E402  (41 AI visibility line)
from workflows_ads import WEEKLY_ADS, weekly_ads_nodes  # noqa: E402  (41 Ads section)


def wf41():
    wf = Workflow(41, "Schedule · Weekly KPI report")
    t = schedule(wf, "Mondays 09:00", "0 9 * * 1")
    p = code(wf, "Last week", r"""
const day = n => { const d = new Date(); d.setUTCDate(d.getUTCDate() - n); return d.toISOString().slice(0, 10); };
return [{ json: { from: day(7), to: day(1) } }];
""")
    k = http(wf, "KPIs", "GET", "={{ $env.ANALYTICS_URL }}/kpis",
             query={"from": "={{ $json.from }}", "to": "={{ $json.to }}", "compare": "true"})
    g = gateway(wf, "Highlights", "weekly_report_highlights",
                "{ kpis: JSON.stringify($json), period: $('Last week').first().json.from + ' to ' + $('Last week').first().json.to }")
    # Numbers into decisions (JTBD #5): clicks, hooks, campaign scorecards and pillar health
    # feed ONE next action per channel. Every source is optional: a failure leaves it out.
    opt = dict(full_response=True, never_error=True, continue_on_fail=True, timeout=60000)
    ins = http(wf, "Clicks by channel", "GET", "={{ $env.CAMPAIGNS_URL }}/insights", query={"days": "7"}, **opt)
    hk = http(wf, "Winning hooks", "GET", "={{ $env.CAMPAIGNS_URL }}/insights/hooks",
              query={"days": "90", "explore": "0", "seed": "1"}, **opt)
    cl = http(wf, "Active campaigns", "GET", "={{ $env.CAMPAIGNS_URL }}/campaigns", query={"status": "active"}, **opt)
    cs = code(wf, "One per campaign", r"""
const b = $input.first().json.body;
const list = (Array.isArray(b) ? b : []).filter(c => c && c.id).slice(0, 10);
const base = $env.CAMPAIGNS_URL;
return list.length ? list.map(c => ({ json: { url: `${base}/campaigns/${c.id}/scorecard` } }))
                   : [{ json: { url: `${base}/health`, noop: true } }];
""")
    sc = http(wf, "Scorecards", "GET", "={{ $json.url }}", **opt)
    sg = code(wf, "Collect scorecards", r"""
const cards = $input.all().map(i => i.json.body).filter(b => b && typeof b === 'object' && b.campaign);
return [{ json: { scorecards: cards.map(c => ({ campaign: c.campaign.name, elapsed_pct: c.elapsed_pct,
  kpis: (c.kpis || []).map(k => ({ metric: k.metric, target: k.target_value, actual: k.actual_value,
    progress_pct: k.progress_pct, state: k.state })) })) } }];
""")
    pl = wf.add("Pillars", "n8n-nodes-base.httpRequest", 4.2, {
        # ENGINE_URL empty: GET the campaign service's /health instead (ignored below).
        "method": "GET",
        "url": "={{ $env.ENGINE_URL ? $env.ENGINE_URL + '/pillars?status=active' : $env.CAMPAIGNS_URL + '/health' }}",
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}}, "timeout": 30000},
    }, onError="continueRegularOutput")
    pp = code(wf, "One per pillar", r"""
const b = $input.first().json.body;
const list = $env.ENGINE_URL && Array.isArray(b) ? b.filter(p => p && p.id).slice(0, 10) : [];
return list.length ? list.map(p => ({ json: { url: `${$env.ENGINE_URL}/pillars/${p.id}/health`, title: p.title } }))
                   : [{ json: { url: `${$env.CAMPAIGNS_URL}/health`, noop: true } }];
""")
    ph = http(wf, "Pillar health", "GET", "={{ $json.url }}", **opt)
    # Paid ads (84): code-computed facts for the prompt and the Ads section. ADS_URL empty: 45's /health, ignored.
    ads = weekly_ads_nodes(wf)
    ad = code(wf, "Action data", WEEKLY_ACTION_DATA)
    ga = gateway(wf, "Next actions", "weekly_actions", "{ data: $json.data, period: $json.period }",
                 continue_on_fail=True)
    ca = code(wf, "Check actions", WEEKLY_CHECK_ACTIONS)
    xp = http(wf, "Experiments", "GET", "={{ $env.CAMPAIGNS_URL }}/experiments", **opt)
    xs = code(wf, "Experiments section", WEEKLY_EXPERIMENTS)
    # Competitor suggestions (78): domains our reviews, mentions and digests keep naming. Never tracked
    # until a person accepts one. AD_LIBRARY_URL empty: 45's /health instead, ignored below.
    cs2 = http(wf, "Competitor suggestions", "POST",
               "={{ $env.AD_LIBRARY_URL ? $env.AD_LIBRARY_URL.replace(/\\/+$/, '') + '/suggestions/scan' : $env.CAMPAIGNS_URL + '/health' }}",
               key=True, **opt)
    ss = code(wf, "Suggestions section", WEEKLY_SUGGESTIONS)
    # AI visibility (82): one line from the latest run. VISIBILITY_URL empty: 45's /health, ignored.
    vi = http(wf, "AI visibility", "GET",
              "={{ $env.VISIBILITY_URL ? $env.VISIBILITY_URL.replace(/\\/+$/, '') + '/summary' : $env.CAMPAIGNS_URL + '/health' }}",
              **opt)
    vs = code(wf, "Visibility section", WEEKLY_VISIBILITY)
    asx = code(wf, "Ads section", WEEKLY_ADS)
    r = http(wf, "Render report", "POST", "={{ $env.REPORT_URL }}/render",
             "={{ JSON.stringify({ title: 'Weekly marketing report', period: { from: $('Last week').first().json.from, to: $('Last week').first().json.to }, kpis: $('KPIs').first().json, highlights_markdown: [$('Highlights').first().json.output, $json.markdown].filter(Boolean).join('\\n\\n') }) }}")
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$('Render report').first().json.markdown.slice(0, 3500)")
    ge = code(wf, "Email set?", r"""
return $env.REPORT_EMAIL_TO ? $('Render report').all() : [];
""", pos=[wf._x - 240, 480])
    em = wf.add("Email report", "n8n-nodes-base.emailSend", 2.1, {
        "fromEmail": "={{ $env.REPORT_EMAIL_FROM || $env.REPORT_EMAIL_TO }}",
        "toEmail": "={{ $env.REPORT_EMAIL_TO }}",
        "subject": "=Weekly marketing report {{ $('Last week').first().json.from }} – {{ $('Last week').first().json.to }}",
        "emailFormat": "html",
        "html": "={{ $json.html }}",
        "options": {},
    }, pos=[wf._x, 480])
    wf.chain(t, p, k, g, ins, hk, cl, cs, sc, sg, pl, pp, ph, ads, ad, ga, ca, xp, xs, cs2, ss, vi, vs, asx, r, gn, n)
    wf.link(r, ge)
    wf.link(ge, em)
    return wf, {
        "summary": "Every Monday it pulls last week's KPIs against the week before (20), has the LLM write plain-language highlights, then adds a **Next actions** section: last week's tracked clicks by channel and top posts (45 `/insights`), the hook styles that earn clicks (45 `/insights/hooks`), the scorecards of active campaigns (45) and, when `ENGINE_URL` is set, the review health of active content pillars (61) go to the LLM (prompt `weekly_actions`), which returns a headline, what changed and at most ONE next action per channel. The workflow checks every action in code: each `why` must cite a number that appears in that data (sign ignored), or the action is dropped and counted; the same check removes unsupported numbers from the headline and the what-changed lines. It renders an HTML report (21) and sends it to your webhook and/or email. Any source that fails is left out; if the LLM fails, the report goes out without the actions. An **Experiments** section follows (45 `/experiments`): running experiments with posts assigned and the next look, decided winners, and the no-difference or inconclusive ones. When `AD_LIBRARY_URL` is set, a **Competitor suggestions** line follows: 78 `POST /suggestions/scan` looks for websites that our reviews (58), social mentions (11) and trend digests (06) name at least twice, stores them as `suggested` with the quotes, and the line lists the ones waiting with the command to accept or ignore them. Nothing is tracked until a person accepts. When `VISIBILITY_URL` is set, an **AI visibility** line follows: from 82 `GET /summary`, how often the brand is named in unaided AI answers per provider (with the change vs the previous run and the citation rate), and how many sentences about the brand the claim checker could not support. When `ADS_URL` is set, an **Ads** section follows: from 84 `GET /summary` (last week vs the week before), spend, conversions, CPL, ROAS, CTR and CPC per platform, per currency in total and for the top campaigns, as finished sentences computed by ads-sync, plus spend not mapped to a campaign and the open pacing/CPL/ROAS/zero-conversion alerts. The same sentences go into the `weekly_actions` data, so an ads action passes the number check only when it quotes them.",
        "schedule": "Mondays 09:00",
        "env": {"NOTIFY_WEBHOOK_URL": "optional", "REPORT_EMAIL_TO": "optional; needs an **SMTP** credential attached to the *Email report* node", "REPORT_EMAIL_FROM": "optional sender",
                "ENGINE_URL": "optional; content engine (61) for pillar health",
                "AD_LIBRARY_URL": "optional; 78-ad-library-sync for competitor suggestions",
                "VISIBILITY_URL": "optional; 82-ai-visibility for the AI visibility line",
                "ADS_URL": "optional; 84-ads-sync for the Ads section"},
        "setup": "Upload analytics first, e.g. a GA4 export:\n\n```bash\ncurl -X POST 'localhost:8120/upload?source=ga4' -H \"X-API-Key: $INTERNAL_API_KEY\" \\\n  -H 'content-type: text/csv' --data-binary @ga4-export.csv\n```",
        "depends": ["03-llm-gateway", "20-analytics-ingest", "21-report-builder", "45-campaign-service",
                    "61-content-engine (optional)"],
    }

# --------------------------------------------------------------------------- 42 kb ingest form

def wf42():
    wf = Workflow(42, "Form · Add to knowledge base")
    t = wf.add("Knowledge form", "n8n-nodes-base.formTrigger", 2.2, {
        "authentication": "basicAuth",
        "formTitle": "Teach the marketing agent",
        "formDescription": "Add product facts, FAQs, policies or a web page. The agent uses these when writing and answering.",
        "formFields": {"values": [
            {"fieldLabel": "Title", "requiredField": True, "placeholder": "e.g. Shipping FAQ"},
            {"fieldLabel": "Page URL", "placeholder": "https://… (optional: fetch this page)"},
            {"fieldLabel": "Text", "fieldType": "textarea", "placeholder": "or paste the content here"},
            {"fieldLabel": "Source", "placeholder": "optional label, e.g. website, sales deck"},
        ]},
        "responseMode": "lastNode",
        "options": {},
    }, webhookId="mkt-knowledge-add", credentials=FORMS_CRED)
    x = http(wf, "Fetch page", "POST", "={{ $env.EXTRACTOR_URL }}/extract",
             "={{ JSON.stringify({ url: $json['Page URL'] || 'none' }) }}", never_error=True)
    p = code(wf, "Document", r"""
const f = $('Knowledge form').first().json;
const page = $input.first().json;
const text = String(f['Text'] || '').trim() || String(page.text || '').trim();
if (!text) throw new Error(f['Page URL'] ? `Could not read ${f['Page URL']}` : 'Paste text or give a page URL');
const id = String(f['Title']).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
return [{ json: { doc_id: id, title: f['Title'], text, source: f['Source'] || f['Page URL'] || 'form' } }];
""")
    kb = http(wf, "Add document", "POST", "={{ $env.KB_URL }}/docs", "={{ JSON.stringify($json) }}", key=True)
    done = wf.add("Done", "n8n-nodes-base.form", 2.3, {
        "operation": "completion", "respondWith": "text", "completionTitle": "Added",
        "completionMessage": "=Saved “{{ $('Document').first().json.title }}” as {{ $json.chunks }} searchable chunks. Adding the same title again replaces it.",
        "options": {},
    })
    wf.chain(t, x, p, kb, done)
    return wf, {
        "summary": "A web form to add facts to the knowledge base (06): paste text or give a URL (fetched with 07). The blog, email and Q&A tools then ground their answers in these facts.",
        "trigger": "n8n form at `<N8N_PUBLIC_URL>/form/mkt-knowledge-add`",
        "depends": ["06-knowledge-base", "07-page-extractor"],
    }

# --------------------------------------------------------------------------- 43 error handler

def wf43():
    wf = Workflow(43, "Error handler", error_workflow=False)
    t = wf.add("On workflow error", "n8n-nodes-base.errorTrigger", 1, {})
    f = code(wf, "Format", r"""
const e = $input.first().json;
const text = [
  `❌ *${e.workflow?.name || 'A workflow'}* failed`,
  `Node: ${e.execution?.lastNodeExecuted || 'unknown'}`,
  `Error: ${e.execution?.error?.message || e.trigger?.error?.message || 'unknown'}`,
  e.execution?.url ? `Execution: ${e.execution.url}` : '',
].filter(Boolean).join('\n');
return [{ json: { text } }];
""")
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, f, gn, n)
    return wf, {
        "summary": "Every other workflow reports failures here (`settings.errorWorkflow`). It posts the workflow name, the failing node, the error and a link to the execution to your webhook.",
        "trigger": "n8n Error Trigger",
        "env": {"NOTIFY_WEBHOOK_URL": "Slack/Discord/Teams incoming webhook. Without it (or Telegram), errors are only in n8n's execution list.",
                "NOTIFY_FORMAT": "`generic` (default, `{text, content}`), `slack`, `discord` or `telegram`; used by every workflow that notifies (`n8nlib.NOTIFY_FORMAT_JS`). Summaries where drafts wait for a person end with the approval-form link",
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID": "for `NOTIFY_FORMAT=telegram` (Bot API `sendMessage`, HTML); `TELEGRAM_API_URL` optionally points to a self-hosted Bot API server"},
        "depends": [],
    }

# --------------------------------------------------------------------------- 24 chat agent

SYSTEM_MESSAGE = """=You are the in-house marketing agent for the brand described in the knowledge base. Today is {{ $now.toFormat('cccc d LLLL yyyy') }}.

How you work:
- For any writing task, CALL A TOOL. Never write marketing copy yourself: the tools apply the brand rules, platform limits and quality checks.
  - blog article → write_blog_post
  - social posts → write_social_posts (pass every channel the user names, comma-separated)
  - search ads → write_ad_copy
  - newsletter → write_email
  - video script (Reels/TikTok/Shorts), landing page copy, or a multi-email nurture sequence → write_content_format
  - SEO plan for a keyword → seo_brief; keyword ideas → keyword_research
  - turn an article/page/text into posts → repurpose_content
  - cut an existing long video (webinar, talk, podcast; a link to the file) into short clips → clip_video
  - analyse a competitor or any web page → research_url
  - start tracking / watching / monitoring a competitor (add it to the competitor list) → track_competitor
  - "is this copy ok?" → check_copy
  - questions about our products, policies, digests, competitors → ask_knowledge_base
  - see or change planned content → content_calendar
  - plan a new campaign (goal, dates, channels) → plan_campaign
  - a month of content / lots of posts over weeks / a content calendar for one topic → plan_content_month
  - list campaigns, their scorecard/results, activate/pause/complete them, set targets → campaigns
- After a tool returns, show the user its result (you may trim it). Keep calendar ids (#12) so they can refer to them.
- Ask one short question only if a required detail is missing (e.g. no topic). Otherwise pick sensible defaults and go.
- Never claim something was published. Content is published only after a human approves it in the approval form.
- Do not invent facts, prices, statistics or customer names."""

TOOLS = [
    ("write_blog_post", 25, "Write a full blog article in the brand voice and save it as a draft.",
     {"topic": "what the article is about", "audience": "who it is for, or empty", "keywords": "SEO keywords, comma-separated, or empty"}),
    ("write_social_posts", 26, "Write NEW social media posts about a topic, one per channel, and save them as drafts. If the user gives text or a link to turn into posts, use repurpose_content instead.",
     {"topic": "what to post about", "channels": "comma-separated: x, linkedin, instagram, facebook, threads, mastodon",
      "link": "URL to include, or empty", "campaign": "campaign name, or empty"}),
    ("write_ad_copy", 27, "Write Google search ad headlines (max 30 chars) and descriptions (max 90 chars).",
     {"product": "what is advertised", "offer": "the offer, or empty", "audience": "target audience, or empty", "keywords": "keywords, or empty"}),
    ("write_email", 28, "Write an email newsletter (subject, preheader, body) and save it as a draft.",
     {"topic": "what the email is about", "audience": "who receives it, or empty", "cta_url": "button link, or empty"}),
    ("write_content_format", 57, "Write a short-form VIDEO SCRIPT (Reels/TikTok/Shorts), a LANDING PAGE, or a multi-email NURTURE SEQUENCE, and save it as a draft. Not for social posts, a single newsletter or a blog article.",
     {"format": "video_script, landing_page or email_sequence",
      "topic": "what the video is about, the product the page sells, or the goal of the sequence",
      "audience": "who it is for, or empty", "offer": "the offer, or empty",
      "details": "facts the user gave for this piece, or empty"}),
    ("seo_brief", 29, "Create an SEO content brief (intent, titles, meta description, outline, FAQs) for a keyword.",
     {"keyword": "target keyword", "competitor_url": "a ranking competitor page, or empty", "audience": "audience, or empty"}),
    ("repurpose_content", 30, "Turn text the user pasted, an article or a web page into social posts (e.g. 'turn this into tweets: ...').",
     {"url": "page to repurpose, or empty", "text": "pasted content, or empty", "channels": "comma-separated channels", "link": "link to include, or empty"}),
    ("research_url", 31, "Analyse any web page (e.g. a competitor): messages, pricing, strengths, weaknesses, opportunities.",
     {"url": "the page URL", "focus": "what to focus on, or empty"}),
    ("track_competitor", 81, "Start TRACKING a competitor: add it to the competitor list so its key pages (pricing, product) are watched for changes and its ads are followed. Not for a one-off analysis of a page (use research_url).",
     {"url": "the competitor's homepage URL", "name": "the competitor's name, or empty",
      "markets": "2-letter countries where we compete, comma-separated, or empty"}),
    ("keyword_research", 32, "Find real search keyword ideas for a seed keyword, grouped by intent.",
     {"seed": "seed keyword"}),
    ("content_calendar", 33, "Read or change the content calendar. action = list | get | create | set_status | schedule.",
     {"action": "list, get, create, set_status or schedule", "id": "item id for get/set_status/schedule, or empty",
      "status": "status filter for list, or new status for set_status (draft, in_review, rejected), or empty",
      "channel": "channel filter or channel for create, or empty", "title": "title for create, or empty",
      "body": "text for create, or empty", "scheduled_at": "ISO 8601 UTC date-time for schedule/create, or empty"}),
    ("ask_knowledge_base", 34, "Answer questions from the knowledge base: our brand, products and policies, the daily morning trend digest, competitor-change notes and uploaded documents.",
     {"question": "the question"}),
    ("plan_campaign", 47, "Plan a new marketing campaign: creates the campaign with targets and a dated plan of posts, then drafts them for review.",
     {"name": "short campaign name", "goal": "what the campaign should achieve",
      "channels": "comma-separated: linkedin, x, instagram, facebook, email, blog",
      "start_date": "start date YYYY-MM-DD", "end_date": "end date YYYY-MM-DD",
      "audience": "who it targets, or empty", "offer": "the offer, or empty",
      "kpis": "targets the user gave, e.g. 20 signups, 500 clicks, or empty", "landing_url": "page the links should point to, or empty"}),
    ("plan_content_month", 64, "Plan a MONTH of content: many dated posts over 4 weeks across several channels from ONE topic or source (article, guide, transcript), fact-checked and drafted daily for review. Not for writing a single piece (use the writing tools). No goal or KPIs: for a campaign use plan_campaign.",
     {"topic": "what the content is about", "channels": "comma-separated: linkedin, x, instagram, facebook, threads, mastodon, blog, email, video",
      "brief": "the angle in a sentence, or empty", "audience": "who it is for, or empty",
      "month": "month YYYY-MM, or empty", "start_date": "start date YYYY-MM-DD, or empty",
      "source_url": "URL of the source article to plan from, or empty", "source_text": "pasted source text to plan from, or empty"}),
    ("campaigns", 53, "List campaigns, show a campaign's scorecard (results vs targets), activate/pause/complete/cancel one, or set a target.",
     {"action": "list, scorecard, activate, pause, complete, cancel or set_target",
      "campaign": "campaign name, slug or id, or empty for list",
      "metric": "for set_target: clicks, sessions, conversions, signups, revenue, ctr, cvr or open_rate, or empty",
      "target_value": "for set_target: the number, or empty"}),
    ("check_copy", 35, "Check copy against brand rules, platform limits and our approved facts; returns problems and a fixed version.",
     {"text": "the copy to check", "channel": "x, linkedin, instagram, facebook, threads, mastodon, blog or email",
      "context": "facts the user gave for this copy, or empty"}),
    ("clip_video", 77, "Cut a long video the user already has (webinar, talk, podcast recording) into short vertical clips with captions and save them for approval. Needs a link to the video file. Not for writing a new video script (write_content_format) or turning text or an article into posts (repurpose_content).",
     {"url": "link to the video or audio file", "max_clips": "how many clips, or empty",
      "channels": "comma-separated: instagram, tiktok, youtube, facebook, x, linkedin, or empty",
      "topic": "what the video is about, or empty"}),
]


def wf24(provider: str = "gateway"):
    """provider: "gateway" (workflow.json: the model through the LLM gateway, so every call shows
    on the Activity page), "local" (variants/direct-ollama.json: n8n talks to Ollama directly,
    the old setup) or "hosted" (variants/hosted.json: a hosted API directly, local fallback)."""
    assert provider in ("gateway", "local", "hosted")
    wf = Workflow(24, "Marketing chat agent")
    subtitle = {"hosted": "Hosted model, local fallback", "local": "Local LLM", "gateway": "Via the LLM gateway"}[provider]
    hello = "running through your LLM gateway" if provider == "gateway" else "running on your local model"
    ch = wf.add("Chat", "@n8n/n8n-nodes-langchain.chatTrigger", 1.4, {
        "public": True,
        "mode": "hostedChat",
        "authentication": "n8nUserAuth",
        "initialMessages": f"Hi! I'm your marketing agent, {hello}.\nTry: \"Write an X and LinkedIn post about our new decaf, link https://example.com/decaf\" or \"What's in the content calendar?\"",
        "options": {"title": "Marketing agent", "subtitle": subtitle + " · drafts only, you approve", "inputPlaceholder": "Ask for a post, a brief, research…"},
    }, pos=[0, 300], webhookId="mkt-marketing-chat")
    ag = wf.add("Marketing agent", "@n8n/n8n-nodes-langchain.agent", 3.1, {
        "promptType": "auto",
        # Hosted: if the provider fails (e.g. Groq free tier's 8k tokens/minute), the turn is
        # finished by the local model instead of erroring. n8n does not retry 429s itself.
        **({"needsFallback": True} if provider == "hosted" else {}),
        "options": {"systemMessage": SYSTEM_MESSAGE, "maxIterations": 6},
    }, pos=[320, 300])
    if provider == "hosted":
        # OpenAI-compatible Chat Completions (Groq by default). The model and base URL are set
        # at import time from CHAT_MODEL / CHAT_BASE_URL; reasoning_effort low keeps gpt-oss
        # fast and stops it from spending the token budget on hidden reasoning.
        lm = wf.add("Hosted model", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.2, {
            "model": {"__rl": True, "mode": "id", "value": "openai/gpt-oss-120b"},
            "options": {"temperature": 0.2, "timeout": 60000, "maxRetries": 2,
                        "extraBody": '{"reasoning_effort": "low"}'},
        }, pos=[0, 560], credentials=HOSTED_CRED)
    if provider == "gateway":
        # OpenAI-compatible Chat Completions on the gateway (03 /v1/chat/completions), which
        # forwards to its provider (Ollama's /v1 by default) and logs the call's metadata. The
        # credential sends INTERNAL_API_KEY as the bearer token and X-Caller: 24 Chat agent.
        # The model must be on the gateway's allowlist (compose adds AGENT_MODEL). Its context
        # (num_ctx 16384) comes from the mkt-agent Modelfile: the /v1 API takes no num_ctx.
        lm = wf.add("Model (via LLM gateway)", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.2, {
            "model": {"__rl": True, "mode": "id", "value": "={{ $env.AGENT_MODEL || 'mkt-agent' }}"},
            "options": {"temperature": 0.2, "timeout": LLM_TIMEOUT, "maxRetries": 1},
        }, pos=[0, 560], credentials=GATEWAY_CHAT_CRED)
    else:
        local = wf.add("Local model (Ollama)", "@n8n/n8n-nodes-langchain.lmChatOllama", 1, {
            "model": "mkt-agent:latest",
            # n8n's default context is 2048 tokens: too small for 11 tool schemas + history.
            "options": {"temperature": 0.2, "numCtx": 16384, "keepAlive": "30m"},
        }, pos=[0, 760] if provider == "hosted" else [0, 560], credentials=OLLAMA_CRED)
        if provider == "hosted":
            wf.link(local, ag, kind="ai_languageModel", dst_index=1)  # fallback input
        else:
            lm = local
    me = wf.add("Chat memory", "@n8n/n8n-nodes-langchain.memoryBufferWindow", 1.3, {
        "contextWindowLength": 8,
    }, pos=[200, 560])
    wf.link(ch, ag)
    wf.link(lm, ag, kind="ai_languageModel")
    wf.link(me, ag, kind="ai_memory")
    for n, (tool, num, desc, args) in enumerate(TOOLS):
        # A $fromAI() without a default is REQUIRED in the tool schema: the model then has to
        # send every argument, and n8n rejects calls that omit irrelevant ones. Optional
        # arguments (described "... or empty") get '' as default.
        fields = {k: ("={{ $fromAI('%s', `%s`, 'string', '') }}" if "or empty" in v
                      else "={{ $fromAI('%s', `%s`, 'string') }}") % (k, v) for k, v in args.items()}
        wf.add(tool, "@n8n/n8n-nodes-langchain.toolWorkflow", 2.2, {
            "description": desc,
            "workflowId": {"__rl": True, "mode": "id", "value": WF_IDS[num]},
            "workflowInputs": mapper(fields),
        }, pos=[420 + 180 * (n % 6), 560 + 200 * (n // 6)])
        wf.link(tool, ag, kind="ai_tool")
    return wf, {
        "summary": f"The agent you talk to. The chat runs on `mkt-agent` (your local Ollama model) through the LLM gateway (03), so every model call shows on the control room's Activity page, with 8 turns of memory and {len(TOOLS)} tools. Each tool is a sub-workflow (see the table), so the model only decides *what* to do and the tools do the work.",
        "trigger": "n8n hosted chat at `<N8N_PUBLIC_URL>/webhook/mkt-marketing-chat/chat` (n8n login required), or **Open chat** in the editor",
        "depends": ["03-llm-gateway (`/v1/chat/completions`)", "02-ollama-models (mkt-agent)", "25–35 sub-workflows",
                    "LLM gateway (chat) credential (imported by 01)"],
        "tools": [(t, num, d) for t, num, d, _ in TOOLS],
    }


ALL = [wf24, wf25, wf26, wf27, wf28, wf29, wf30, wf31, wf32, wf33, wf34, wf35,
       wf36, wf37, wf38, wf39, wf40, wf41, wf42, wf43]
