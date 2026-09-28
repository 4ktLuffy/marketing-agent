"""Phase 4 workflows (64, 65): the content engine's n8n side (plan a month, draft it daily).

The engine service (61) plans in code and checks novelty; these workflows do what needs the
LLM and the other services: atoms from the pillar (gateway), a claim check per atom (44),
calendar idea items per slot (19), and the daily drafting through the existing prompts and
the quality gate (35). Design: _dev/research/content-volume.md section 4.
"""
from n8nlib import (OPS_TO_ITEMS, RAW_OPS, Workflow, call_workflow, code, dynamic_http, gateway, http, if_true, loop_one_by_one,
                    notify, schedule, staged_ops, sub_trigger, GATE_NOTIFY)
from workflows import CARD_REQUEST, IMAGE_URL_OF, CLAIM_TIMEOUT, VIDEO_REQUEST, VIDEO_RESULT, brand_profile, image_card, video_render

ENGINE_CHANNELS = "['linkedin', 'x', 'instagram', 'facebook', 'threads', 'mastodon', 'blog', 'email', 'video']"
HOOKS = "['question', 'fact_led', 'story', 'how_to', 'benefit', 'contrarian']"
ERR_TEXT = r"""
const errText = j => { j = j || {}; const e = j.error ?? j.detail ?? j.body?.detail; return e == null ? '' : String(typeof e === 'object' ? e.message || JSON.stringify(e) : e).slice(0, 200); };
"""

# --------------------------------------------------------------------------- 64 plan a month

PLAN_PREPARE = r"""
const s = $input.first().json;
const ENGINE = %s;
// The chat model paraphrases channel names: map what it means, report what we can't plan.
const ALIAS = { twitter: 'x', 'x/twitter': 'x', tweets: 'x', ig: 'instagram', insta: 'instagram', fb: 'facebook',
  newsletter: 'email', emails: 'email', mail: 'email', reels: 'video', reel: 'video', tiktok: 'video', shorts: 'video',
  youtube: 'video', videos: 'video', video_script: 'video', article: 'blog', articles: 'blog', 'blog posts': 'blog' };
const bad = result => [{ json: { ok: false, result } }];
// Core install (no growth profile): the content engine (61) is not running and ENGINE_URL is empty.
if (!$env.ENGINE_URL) return bad('Planning a month needs the content engine (61-content-engine), which is not installed here: ENGINE_URL is empty on n8n. Install the growth profile (01-marketing-stack/scripts/install.sh --profile growth).');
const topic = String(s.topic || '').trim();
if (!topic) return bad('What should the month of content be about? Give a topic, and ideally the long piece to plan from (paste it, or a URL).');
const raw = String(s.channels || '').split(/,|;|\/| and |&/).map(c => c.trim().toLowerCase()).filter(Boolean);
const channels = [...new Set(raw.map(c => ALIAS[c] || c).filter(c => ENGINE.includes(c)))];
const unknown = raw.filter(c => !ENGINE.includes(ALIAS[c] || c));
if (!channels.length) return bad(`Which channels should the month cover? For example "linkedin, x, blog". Possible: ${ENGINE.join(', ')}.`);
const startRaw = String(s.start_date || '').trim();
const start_date = /^\d{4}-\d{2}-\d{2}$/.test(startRaw) ? startRaw : null;
// Month: YYYY-MM, or "October 2026" / "october", else the start date's month, else next month.
const NAMES = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
let month = String(s.month || '').trim().toLowerCase();
const named = month.match(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{4})?/);
const now = new Date();
if (/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) { /* as given */ }
else if (named) {
  const m = NAMES.indexOf(named[1]);
  let y = named[2] ? Number(named[2]) : now.getUTCFullYear();
  if (!named[2] && m < now.getUTCMonth()) y += 1;   // "march" in September = next March
  month = `${y}-${String(m + 1).padStart(2, '0')}`;
} else if (start_date) month = start_date.slice(0, 7);
else { const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1)); month = d.toISOString().slice(0, 7); }
const url = String(s.source_url || '').trim();
if (url && !/^https?:\/\/\S+$/.test(url)) return bad(`"${url}" is not a web address (it must start with http:// or https://).`);
return [{ json: { ok: true, topic: topic.slice(0, 300), brief: String(s.brief || '').trim().slice(0, 5000) || topic.slice(0, 300),
  audience: String(s.audience || '').trim().slice(0, 1000) || 'our usual audience (see the brand profile)',
  channels, unknown, month, start_date, source_url: url || null, source_text: String(s.source_text || '').trim() } }];
""" % ENGINE_CHANNELS

PLAN_SOURCE = ERR_TEXT + r"""
const p = $('Prepare').first().json;
const page = $input.first().json || {};
// The local 7B model extracts atoms from at most this much text, and the claim checker gets it
// as evidence for every atom (its limit is 20,000 characters).
const MAX = 12000;
let source = p.source_text || (p.source_url ? String(page.text || '').trim() : '');
if (p.source_url && !p.source_text && !source)
  throw new Error(`Could not read ${p.source_url} (${errText(page) || 'no text'}). Paste the text instead.`);
const cut = source.length > MAX;
return [{ json: { ...p, source: source.slice(0, MAX), cut, source_title: page.title || null } }];
"""

PLAN_SCREEN = r"""
// Screen the model's atoms IN CODE before the (slow) claim check: an faq/objection that ends
// with "?" has no answer (the prompt says so and the model still does it sometimes).
const pillar = $('Create pillar').first().json;
if (!pillar.id) throw new Error(`Pillar not created: ${JSON.stringify(pillar.detail || pillar).slice(0, 300)}`);
const KINDS = ['claim', 'story', 'faq', 'tip', 'stat', 'objection', 'quote'];
const atoms = ((($input.first().json || {}).output || {}).atoms) || [];
const dropped = [], keep = [], seen = new Set();
for (const a of atoms) {
  const text = String((a && a.text) || '').trim();
  const kind = String((a && a.kind) || '').trim().toLowerCase();
  if (!text || !KINDS.includes(kind)) { dropped.push({ kind, text, why: 'no text or unknown kind' }); continue; }
  if ((kind === 'faq' || kind === 'objection') && /\?\s*$/.test(text)) { dropped.push({ kind, text, why: `${kind} without an answer (ends with "?")` }); continue; }
  const norm = text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  if (seen.has(norm)) { dropped.push({ kind, text, why: 'duplicate' }); continue; }
  seen.add(norm);
  const ev = a.evidence == null || a.evidence === '' ? null : (typeof a.evidence === 'string' ? a.evidence : JSON.stringify(a.evidence));
  keep.push({ kind, text: text.slice(0, 1000), evidence: ev ? ev.slice(0, 2000) : null, promo: a.promo === true });
}
if (!keep.length) throw new Error(`The model returned no usable atoms (${atoms.length} returned, all dropped).`);
const screened = { returned: atoms.length, dropped };
return keep.map((a, i) => ({ json: { ...a, n: i + 1, screened: i === 0 ? screened : undefined } }));
"""

PLAN_RECORD_CHECK = ERR_TEXT + r"""
const a = $('Check atoms').first(1).json;   // loop output 1 = the atom checked in this round
const r = $input.first().json || {};
const checked = typeof r.ok === 'boolean';
const reasons = (r.claims || []).filter(c => c && c.supported === false).flatMap(c => c.reasons || []);
const why = !checked ? `claim checker unavailable (${errText(r) || 'no answer'})`
  : r.ok ? '' : (reasons.join('; ') || (r.unsupported || []).join('; ') || 'not supported by the source or the facts');
return [{ json: { ...a, verified: r.ok === true, checked, why: why.slice(0, 300) } }];
"""

PLAN_ATOMS_PAYLOAD = r"""
const rows = $input.all().map(i => i.json);
return [{ json: { body: { atoms: rows.map(r => ({ kind: r.kind, text: r.text, verified: r.verified,
  evidence: r.evidence, promo: r.promo })) }, rows } }];
"""

PLAN_ORPHANS = OPS_TO_ITEMS + r"""
// A re-plan deletes the pillar's still-planned slots; 61 returns their idea items, which
// would otherwise stay in the calendar as orphans. They are rejected, not deleted.
const ids = ((($input.first().json || {}).body || {}).removed_calendar_item_ids) || [];
return opsToItems(ids.filter(Number.isInteger).map(id => ({ method: 'POST', url: `${$env.CALENDAR_URL}/items/${id}/status`,
  body: { status: 'rejected', note: 're-planned' } })), $env.CALENDAR_URL);
"""

PLAN_IDEAS = ERR_TEXT + r"""
const res = $('Plan').first().json;   // full response
const pid = $('Create pillar').first().json.id;
const plan = res.body || {};
if (res.statusCode !== 200) return [{ json: { planned: false, detail: errText(res) || `plan failed (${res.statusCode})` } }];
const slots = Array.isArray(plan.slots) ? plan.slots : [];
if (!slots.length) return [{ json: { planned: false, detail: 'the plan has no slots (see unfilled)' } }];
const words = (t, n) => { const w = String(t).replace(/\s+/g, ' ').trim().split(' '); return w.slice(0, n).join(' ') + (w.length > n ? '…' : ''); };
// One `idea` item per slot. The notes line is how 38 (outcomes), 40 (skips engine days) and
// 65 (drafter) recognise engine items: keep its shape.
return slots.map(s => ({ json: { planned: true, slot_id: s.id, item: {
  title: `${s.hook_style}: ${words(s.atom.text, 10)}`.slice(0, 120), channel: s.channel, status: 'idea',
  body: `[${s.format} · ${s.hook_style}] ${s.atom.text}` + (s.atom.evidence ? `\nEvidence: ${s.atom.evidence}` : ''),
  scheduled_at: `${s.date}T${s.time_utc}:00Z`, hook_style: s.hook_style, campaign: `engine-p${pid}`,
  notes: `engine pillar #${pid} slot #${s.id} atom #${s.atom_id} (${s.atom.kind})` } } }));
"""

PLAN_ATOM_LINES = r"""
function atomLines() {
  const pillar = $('Create pillar').first().json;
  const src = $('Source').first().json;
  const scr = $('Screen atoms').first().json.screened;
  const rows = $('Atoms payload').first().json.rows;
  const saved = $('Save atoms').first().json;
  const kept = rows.filter(r => r.verified);
  const unsupported = rows.filter(r => r.checked && !r.verified);
  const unchecked = rows.filter(r => !r.checked);
  const kinds = {};
  for (const r of kept) kinds[r.kind] = (kinds[r.kind] || 0) + 1;
  const lines = [
    `Pillar #${pillar.id} "${pillar.title}" for ${pillar.month}` + (src.cut ? ' (source cut to its first 12,000 characters)' : '') + '.',
    `Atoms: ${scr.returned} extracted, **${kept.length} verified and kept** (${Object.entries(kinds).map(([k, n]) => `${n} ${k}`).join(', ')}), ` +
      `${scr.dropped.length + unsupported.length + unchecked.length} dropped.`,
  ];
  const why = {};
  for (const d of scr.dropped) why[d.why] = (why[d.why] || 0) + 1;
  for (const [w, n] of Object.entries(why)) lines.push(`- ${n} dropped before checking: ${w}`);
  if (unsupported.length) lines.push(`- ${unsupported.length} not supported by the source or the approved facts, e.g.:`,
    ...unsupported.slice(0, 4).map(r => `  - "${r.text.slice(0, 90)}" (${r.why.slice(0, 120)})`));
  if (unchecked.length) lines.push(`- ${unchecked.length} could not be checked (claim checker unavailable), so they are not used`);
  const skipped = Array.isArray(saved.skipped) ? saved.skipped.length : Number(saved.skipped) || 0;
  if (skipped) lines.push(`- ${skipped} already stored for this pillar`);
  return { lines, pillar, saved };
}
"""

PLAN_REPLY = PLAN_ATOM_LINES + r"""
const { lines, pillar } = atomLines();
const plan = $('Plan').first().json.body;
const ideas = $('Add ideas').all().map(i => i.json);
const linkFails = $('Record slot items').all().filter(r => !r.json.noop && r.json.statusCode >= 300).length;
const orphans = $('Reject orphans').all().filter(r => !r.json.noop);
const orphanFails = orphans.filter(r => r.json.statusCode >= 300).length;
const unknown = $('Prepare').first().json.unknown;
const fmt = d => new Date(d + 'T00:00:00Z').toUTCString().slice(0, 11);
lines.push('', `Plan ${fmt(plan.start_date)} – ${fmt(plan.end_date)} (${plan.weeks} weeks), ${plan.slots.length} slots:`);
for (const ch of Object.keys(plan.planned)) {
  const days = plan.slots.filter(s => s.channel === ch).map(s => s.date.slice(5));
  lines.push(`- ${ch}: ${plan.planned[ch]} of ${plan.requested[ch]}` + (plan.unfilled[ch] ? ` (${plan.unfilled[ch]} unfilled: not enough verified atoms)` : '') +
    (days.length ? ` · ${days.join(', ')}` : ''));
}
if (unknown.length) lines.push(`Not planned (unknown channel): ${unknown.join(', ')}.`);
const ids = ideas.map(i => i.id).filter(Boolean);
lines.push('', `Calendar: ${ids.length} idea items${ids.length ? ` (#${Math.min(...ids)}–#${Math.max(...ids)})` : ''}, each with its date, channel and hook style.` +
  (linkFails ? ` ${linkFails} slots could not be linked to their item.` : '') +
  (orphans.length ? ` Re-plan: ${orphans.length - orphanFails} idea items of replaced slots moved to rejected` +
    (orphanFails ? ` (${orphanFails} could not be moved)` : '') + '.' : ''),
  'The engine drafter (65) writes them each morning a few days ahead; drafts that pass the quality gate appear in the approval form. Nothing is published until a person approves it.');
return [{ json: { result: lines.join('\n') } }];
"""

PLAN_THIN = PLAN_ATOM_LINES + r"""
const { lines, pillar, saved } = atomLines();
lines.push('', `No month planned: ${$json.detail}`,
  `The plan needs at least ${saved.min_atoms || 12} verified atoms. Give a longer, more substantial source (an article, guide or transcript of 1,200+ words) and ask again.`);
return [{ json: { result: lines.join('\n') } }];
"""


def wf64():
    wf = Workflow(64, "Tool · Content engine (plan a month)")
    s = sub_trigger(wf, [(k, "string") for k in (
        "topic", "brief", "audience", "channels", "month", "start_date", "source_url", "source_text")])
    p = code(wf, "Prepare", PLAN_PREPARE)
    ok = if_true(wf, "Can plan?", "={{ $json.ok }}")
    bad = code(wf, "Explain", "return [{ json: { result: $input.first().json.result } }];", pos=[wf._x, 520])
    x = http(wf, "Fetch source", "POST", "={{ $env.EXTRACTOR_URL }}/extract",
             "={{ JSON.stringify({ url: $json.source_text ? 'none' : ($json.source_url || 'none') }) }}",
             never_error=True, continue_on_fail=True, timeout=60000)
    src = code(wf, "Source", PLAN_SOURCE)
    cp = http(wf, "Create pillar", "POST", "={{ $env.ENGINE_URL }}/pillars",
              "={{ JSON.stringify({ title: $json.topic, brief: $json.brief, audience: $json.audience, "
              "source_text: $json.source || null, source_url: $json.source_url, channels: $json.channels, "
              "month: $json.month, promo_max: 0.2 }) }}", key=True)
    # The gateway adds the approved facts (vars.facts) to every prompt that declares them.
    at = gateway(wf, "Extract atoms", "pillar_atoms",
                 "{ pillar_title: $('Source').first().json.topic, brief: $('Source').first().json.brief, "
                 "audience: $('Source').first().json.audience, source_text: $('Source').first().json.source || null }")
    sc = code(wf, "Screen atoms", PLAN_SCREEN)
    # One claim check at a time: parallel requests queue in Ollama and the last ones time out.
    lp = loop_one_by_one(wf, "Check atoms")
    cc = http(wf, "Claim check", "POST", "={{ $env.CLAIMS_URL }}/verify",
              "={{ JSON.stringify({ text: $json.text, context: [$('Source').first().json.topic, "
              "$('Source').first().json.brief, $('Source').first().json.source].filter(Boolean).join('\\n') }) }}",
              key=True, never_error=True, continue_on_fail=True, timeout=CLAIM_TIMEOUT, pos=[wf._x, 520])
    rc = code(wf, "Record check", PLAN_RECORD_CHECK, pos=[wf._x + 240, 520])
    ap = code(wf, "Atoms payload", PLAN_ATOMS_PAYLOAD)
    sa = http(wf, "Save atoms", "POST", "={{ $env.ENGINE_URL }}/pillars/{{ $('Create pillar').first().json.id }}/atoms",
              "={{ JSON.stringify($json.body) }}", key=True)
    pl = http(wf, "Plan", "POST", "={{ $env.ENGINE_URL }}/pillars/{{ $('Create pillar').first().json.id }}/plan",
              "={{ JSON.stringify({ start_date: $('Source').first().json.start_date, weeks: 4 }) }}",
              key=True, never_error=True, full_response=True)
    orp = code(wf, "Orphan ideas", PLAN_ORPHANS)
    ro = dynamic_http(wf, "Reject orphans")
    ci = code(wf, "Calendar ideas", PLAN_IDEAS)
    pd = if_true(wf, "Planned?", "={{ $json.planned }}")
    ad = http(wf, "Add ideas", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json.item) }}", key=True)
    lk = code(wf, "Slot items", r"""
// The slot stays `planned`; it only records its idea item, which the drafter (65) fills.
const ideas = $('Calendar ideas').all();
return $input.all().map((r, i) => ({ json: { method: 'POST', url: `${$env.ENGINE_URL}/slots/${ideas[i].json.slot_id}/status`,
  body: JSON.stringify({ status: 'planned', calendar_item_id: r.json.id }) } }));
""")
    rs = dynamic_http(wf, "Record slot items")
    rp = code(wf, "Reply", PLAN_REPLY)
    th = code(wf, "Too thin", PLAN_THIN, pos=[wf._x - 720, 520])
    wf.chain(s, p, ok)
    wf.link(ok, x, src_index=0)
    wf.link(ok, bad, src_index=1)
    wf.chain(x, src, cp, at, sc, lp)
    wf.link(lp, ap, src_index=0)     # done: every atom with its check
    wf.link(lp, cc, src_index=1)     # one atom per round
    wf.chain(cc, rc)
    wf.link(rc, lp)
    wf.chain(ap, sa, pl, orp, ro, ci, pd)
    wf.link(pd, ad, src_index=0)
    wf.link(pd, th, src_index=1)
    wf.chain(ad, lk, rs, rp)
    return wf, {
        "summary": "Plans a month of content from one pillar (a topic plus, ideally, the long piece it comes from: pasted text or a URL read by 07). It creates the pillar in the content engine (61), extracts 15–30 small self-contained ideas (\"atoms\": claims, stories, FAQs with their answer, tips, stats, objections, quotes) with the `pillar_atoms` prompt through the gateway (03, with the approved facts), drops in code the FAQ/objection atoms without an answer, and checks every atom with the claim checker (44), one at a time, with the pillar as evidence. Only verified atoms are stored as usable. 61 then plans 4 weeks in code (atoms × hook styles × formats × channels, platform cadences, no repeats) and this workflow creates one `idea` item per slot in the calendar (19) (on a re-plan, the idea items of the planned slots 61 replaced are moved to `rejected` with the note `re-planned`), with its date, channel, hook style and the note `engine pillar #P slot #S atom #A (<kind>)`. The drafter (65) writes those items each morning. It takes a while on a local model: one LLM call for the atoms plus one claim check per atom (tens of minutes for 25 atoms).",
        "inputs": {"topic": "what the pillar is about (its title)", "brief": "optional: one or two sentences on the angle (default: the topic)",
                   "audience": "optional: who it is for", "channels": "comma list: linkedin, x, instagram, facebook, threads, mastodon, blog, email, video",
                   "month": "optional: `YYYY-MM` or a month name (default: the start date's month, else next month)",
                   "start_date": "optional: `YYYY-MM-DD` the 4 weeks start on (default: the 1st of the month)",
                   "source_url": "optional: page with the pillar (read with 07)", "source_text": "optional: the pillar text itself (wins over the URL)"},
        "output": "`{result}`: atoms kept and dropped (with reasons), slots per channel against the cadence, unfilled slots, dates, calendar ids. A pillar with fewer than 12 verified atoms is stored but not planned, and the reply says why",
        "depends": ["61-content-engine", "03-llm-gateway", "04-prompt-library (prompt `pillar_atoms`)", "07-page-extractor", "44-claim-checker", "19-content-calendar"],
        "called_by": "the chat agent (24), tool `plan_content_month`",
        "env": {"ENGINE_URL": "the content engine (61), default `http://content-engine:8000`. Empty (core install) = the tool answers that the content engine is not installed"},
        "test": {"topic": "Why remote teams need a coffee ritual", "brief": "", "audience": "people-ops leads at remote companies",
                 "channels": "linkedin, x", "month": "", "start_date": "", "source_url": "",
                 "source_text": "Remote teams lose the small talk that happens by the office kitchen. A fixed weekly coffee call brings some of it back: same time, cameras on, no agenda."},
    }


# --------------------------------------------------------------------------- 65 engine drafter

DRAFT_PICK = r"""
const num = (v, d) => { const n = Number(v); return v === undefined || v === null || v === '' || !Number.isFinite(n) || n < 0 ? d : n; };
const CAP = Math.floor(num($env.ENGINE_DRAFTS_PER_RUN, 12));
const AHEAD = num($env.ENGINE_LOOKAHEAD_DAYS, 7);
const list = r => { const raw = [].concat((r || {}).body ?? []); return (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(x => x && x.id); };
// Paused pillars (the stop rule in 61) are not listed as active, so their slots wait.
const pillars = Object.fromEntries(list($('Active pillars').first().json).filter(p => p.status === 'active').map(p => [p.id, p]));
const ideas = new Map(list($('Idea items').first().json).map(i => [i.id, i]));
const today = new Date().toISOString().slice(0, 10);
const until = new Date(Date.now() + AHEAD * 864e5).toISOString().slice(0, 10);
const slots = $input.all().flatMap(r => Array.isArray(r.json.body) ? r.json.body : [])
  .filter(s => s && s.status === 'planned' && pillars[s.pillar_id])
  .sort((a, b) => a.date.localeCompare(b.date) || a.time_utc.localeCompare(b.time_utc) || a.id - b.id);
const notes = [], ready = [];
const past = slots.filter(s => s.date < today).length;
if (past) notes.push(`${past} planned slot${past === 1 ? ' is' : 's are'} already past their date and were not drafted`);
const noItem = [];
for (const s of slots.filter(s => s.date >= today && s.date <= until)) {
  const it = ideas.get(s.calendar_item_id);
  if (!s.calendar_item_id) { noItem.push(s.id); continue; }
  if (!it) { notes.push(`slot #${s.id}: calendar item #${s.calendar_item_id} is no longer an idea, skipped`); continue; }
  ready.push({ slot: s, item: it, pillar: pillars[s.pillar_id] });
}
if (noItem.length) notes.push(`${noItem.length} slot(s) have no calendar item (not planned by plan_content_month), e.g. #${noItem.slice(0, 5).join(', #')}`);
const picked = ready.slice(0, CAP);
if (ready.length > picked.length) notes.push(`${ready.length - picked.length} more slots in the next ${AHEAD} days wait for the next run (ENGINE_DRAFTS_PER_RUN=${CAP})`);
if (picked.length) return picked.map(x => ({ json: { pick: true, ...x, notes } }));
return [{ json: { pick: false, notes } }];
"""

DRAFT_REQUEST = r"""
// Reached from Examples (first attempt) or from "Retry?" (the draft was too close to an
// earlier post on the channel: one more try with a different hook).
const cur = $('Loop').first(1).json;     // loop output 1 = the slot drafted in this round
const inp = $input.first().json;
const retry = inp.retry === true;
const s = cur.slot, pl = cur.pillar || {}, atom = s.atom || {};
// A slot in a running experiment (45) carries its arm: the hook is fixed by a hook_style
// arm (a retry keeps it), cta and length arms are instructions; format and time are set by 61.
const x = s.experiment || null;
const forced_hook = !!(x && x.variable === 'hook_style');
const hook = retry && !forced_hook ? inp.next_hook : s.hook_style;
const armNote = !x ? '' : x.variable === 'cta' ? `Call to action (required, experiment arm ${x.arm}): ${x.value}.${x.brief ? ' ' + x.brief : ''}`
  : x.variable === 'length' ? `Length (required, experiment arm ${x.arm}): ${x.value}.${x.brief ? ' ' + x.brief : ''}`
  : x.brief ? `Writer note (experiment arm ${x.arm}): ${x.brief}` : '';
const exRaw = ($('Examples').first().json || {}).body;
const examples = (Array.isArray(exRaw) ? exRaw : []).filter(e => e && e.text).map(e => `- ${e.text}`).join('\n') || null;
// The atom was verified against the pillar (64), so it and its evidence are the facts.
const facts = [`Idea: ${atom.text}`, atom.evidence ? `Source: ${atom.evidence}` : '',
  `Pillar: ${pl.title}. ${pl.brief || ''}`, armNote].filter(Boolean).join('\n');
const opening = `Open with a ${String(hook).replace('_', ' ')} hook.`;
let request, gateChannel = s.channel, rewrite = '';
if (s.format === 'blog') {
  request = { prompt: 'blog_post', vars: { topic: atom.text, audience: pl.audience || null, context: `${facts}\n${opening}`, length_words: 700 } };
} else if (s.format === 'email') {
  request = { prompt: 'email_newsletter', vars: { topic: atom.text, audience: pl.audience || null, context: `${facts}\n${opening}` } };
} else if (s.format === 'video_script') {
  // Structured: the gate only reports (a rewrite merged a script's parts, night 5).
  request = { prompt: 'video_script', vars: { topic: atom.text, audience: pl.audience || null, context: `${facts}\n${opening}` } };
  gateChannel = 'video'; rewrite = 'no';
} else if (s.format === 'thread') {
  // Own prompt: asked through social_posts with a format note, the 7B model wrote one post
  // (0/9 threads with 3+ parts in the eval, 23 threads_carousels). Code numbers the parts.
  request = { prompt: 'x_thread', vars: { topic: atom.text, audience: pl.audience || null, context: facts, examples, prefer_hooks: hook } };
  // 14 has no thread limit: the whole thread would fail x's 280 characters. After Gate checks each part.
  // Report-only: in the e2e test a gate rewrite (for too many emoji) merged the 4 parts into one.
  gateChannel = 'x_thread'; rewrite = 'no';
} else if (s.format === 'carousel_text') {
  request = { prompt: 'carousel_text', vars: { topic: atom.text, channel: s.channel, audience: pl.audience || null,
    context: facts, examples, prefer_hooks: hook } };
  rewrite = 'no';   // structured like a video script: a rewrite would merge the slides; the gate reports
} else {
  request = { prompt: 'social_posts', vars: { topic: atom.text,
    channels: s.channel, context: facts, campaign: `engine-p${s.pillar_id}`, examples, prefer_hooks: hook } };
}
return [{ json: { slot_id: s.id, item_id: cur.item.id, pillar_id: s.pillar_id, channel: s.channel, format: s.format,
  hook, forced_hook, experiment: x, attempt: retry ? 2 : 1, first_reasons: retry ? inp.reasons : [], gateChannel, rewrite, facts, request } }];
"""

DRAFT_TEXT = ERR_TEXT + r"""
const r = $('Request').first().json;
const res = $input.first().json || {};
const out = res.output;
let text = '', prefix = '', suffix = '', title = '', error = '', slides = null, gateText = null, script = null;
const tag = h => '#' + String(h).replace(/^#/, '').replace(/\s+/g, '');
try {
  if (out == null) throw new Error(errText(res) || 'no answer from the gateway');
  if (r.format === 'blog') {
    text = String(out).trim();
    title = ((text.match(/^#\s+(.+)$/m) || [])[1] || '').trim();
  } else if (r.format === 'email') {
    prefix = `Subject: ${out.subject}\nPreheader: ${out.preheader}\n\n`;
    text = String(out.body_markdown || '').trim();
    title = `Email: ${out.subject}`;
  } else if (r.format === 'video_script') {
    const lines = [`Hook: ${out.hook.spoken}`, `On screen: ${out.hook.on_screen}`, ''];
    for (const b of out.beats) lines.push(`Say: ${b.spoken}`, `On screen: ${b.on_screen}`, '');
    lines.push(`Close: ${out.cta}`, '', `Caption: ${out.caption}`, (out.hashtags || []).map(tag).join(' '));
    text = lines.join('\n').trim();
    suffix = ['', '', `Estimated length: ${out.estimated_seconds} s`, 'Shot list:',
      ...out.beats.map((b, i) => `- beat ${i + 1}: ${b.shot}`)].join('\n');
    title = `Video script: ${out.hook.spoken}`;
    script = out;   // rendered as a preview MP4 by 71 after the gate
  } else if (r.format === 'thread') {
    // The model does not number the posts (the prompt says so); strip any "1/" it added anyway.
    const parts = (out.posts || []).map(p => String(p || '').replace(/^\s*(\(?\d+\s*[\/.)](?!\d)|\d+\s*\/\s*\d+)\s*/, '').trim()).filter(Boolean);
    if (parts.length < 2) throw new Error(`thread has ${parts.length} post(s)`);
    text = parts.map((p, i) => `${i + 1}/${parts.length} ${p}`).join('\n\n');
    gateText = parts.join('\n\n');   // the claim checker should not read "1/3" as part of a claim
    title = `Thread: ${parts[0]}`;
  } else if (r.format === 'carousel_text') {
    const sl = (out.slides || []).filter(s => s && String(s.title || '').trim());
    if (sl.length < 3) throw new Error(`carousel has ${sl.length} slide(s)`);
    const clean = t => String(t || '').replace(/^\s*(slide\s*)?\d+\s*[:.)\-–—]\s*/i, '').trim();
    // The last slide must ask for the action. The 7B model fills "cta" reliably but often ends
    // the slides on a benefit (eval 23 threads_carousels: last slide a CTA in 4 of 9 runs, the
    // cta field in 9 of 9), so code adds the cta as the last slide when it is missing.
    const cta = String(out.cta || '').trim();
    const CTA = /\b(follow|save|share|comment|subscribe|order|shop|try|start|get|grab|visit|check|join|sign up|book|tell|dm|message|reply|click|tap|learn|read|explore|discover|pick|choose|buy|head|link in bio|bio)\b|https?:\/\//i;
    const last = sl[sl.length - 1];
    if (cta && !CTA.test(`${last.title} ${last.body}`)) {
      const slide = { title: cta.length <= 40 ? cta : 'Your next step', body: cta.length <= 40 ? '' : cta.slice(0, 160) };
      if (sl.length < 8) sl.push(slide); else sl[sl.length - 1] = slide;
    }
    text = [...sl.map((s, i) => `Slide ${i + 1} — ${clean(s.title)}\n${String(s.body || '').trim()}`.trim()),
      `Caption: ${String(out.caption || '').trim()}`].join('\n\n');
    title = `Carousel: ${clean(sl[0].title)}`;
    slides = sl.map(s => clean(s.title));   // one title card per slide (17)
    // The gate checks the words, without the "Slide k —" labels (the claim checker read
    // "Slide 3 — Skip or pause" as a claim).
    gateText = [...sl.map(s => `${clean(s.title)}. ${String(s.body || '').trim()}`.trim()), String(out.caption || '').trim()].join('\n');
  } else {
    const posts = out.posts || [];
    const post = posts.find(p => String(p.channel || '').trim().toLowerCase() === r.channel) || posts[0];
    if (!post || !String(post.text || '').trim()) throw new Error(`no ${r.channel} post in the answer`);
    text = String(post.text).trim();
  }
  if (!text) throw new Error('empty draft');
} catch (e) { error = String(e.message || e).slice(0, 200); }
if (!title) title = text.split('\n').map(l => l.replace(/^#+\s*/, '').trim()).find(l => /[\p{L}\p{N}]/u.test(l)) || '';
return [{ json: { ...r, text, prefix, suffix, title: title.slice(0, 120), error, slides, script, gateText: gateText || text } }];
"""

DRAFT_JUDGE = ERR_TEXT + r"""
const d = $('Draft text').first().json;
const n = $input.first().json || {};
const HOOKS = %s;
if (d.error) return [{ json: { ...d, route: 'error' } }];
if (typeof n.novel !== 'boolean')
  return [{ json: { ...d, route: 'gate', novelty_note: `novelty NOT checked (${errText(n) || 'engine unavailable'})` } }];
if (n.novel) return [{ json: { ...d, route: 'gate', novelty_note: n.embedding_checked ? '' : 'novelty checked without embeddings (weaker check)' } }];
const reasons = (n.reasons || []).map(String);
if (d.attempt === 1) {
  // A different hook, as far from the planned one as the list allows; an experiment's hook
  // arm is kept (the retry then only rewrites the text).
  const i = Math.max(0, HOOKS.indexOf(d.hook));
  return [{ json: { route: 'retry', retry: true, next_hook: d.forced_hook ? d.hook : HOOKS[(i + 3) %% HOOKS.length], reasons } }];
}
return [{ json: { ...d, route: 'drop', reasons: [...(d.first_reasons || []), ...reasons] } }];
""" % HOOKS

DRAFT_AFTER_GATE = r"""
const d = $('Judge').first().json;
const g = $input.first().json || {};
// Report-only formats keep their own text (the gate may have been given a plain version).
const core = d.rewrite !== 'no' && typeof g.text === 'string' && g.text.trim() ? g.text.trim() : d.text;
const problems = Array.isArray(g.problems) ? [...g.problems] : ['quality gate gave no result'];
if (d.format === 'thread') {
  // Each part of an X thread is its own post: 280 characters, a URL counts as 23.
  const len = t => t.replace(/https?:\/\/\S+/g, 'x'.repeat(23)).length;
  const long = core.split(/\n\s*\n/).map(t => t.trim()).filter(t => t && len(t) > 280);
  if (long.length) problems.push(`${long.length} thread part(s) over 280 characters, e.g. "${long[0].slice(0, 40)}…"`);
}
return [{ json: { ...d, core, body: d.prefix + core + d.suffix, ok: g.ok === true && problems.length === 0, problems,
  warnings: [...(g.warnings || []), d.novelty_note].filter(Boolean), rewritten: g.rewritten === true } }];
"""

DRAFT_SAVE_OPS = RAW_OPS + r"""
const r = $input.first().json;
const base = $env.CALENDAR_URL;
const id = r.item_id;
const patch = { title: r.title || undefined, body: r.body, hook_style: r.hook };
if (r.image_url) patch.image_url = r.image_url;
if (r.video_url) patch.video_url = r.video_url;
const ops = [{ stage: 1, method: 'PATCH', url: `${base}/items/${id}`, body: patch }];
const why = [r.ok ? 'drafted by the engine drafter' : `drafted by the engine; needs a human. Quality gate: ${r.problems.join('; ')}`,
  r.attempt === 2 ? `second attempt with hook ${r.hook} (the first was too close to an earlier post)` : '',
  ...r.warnings, r.image_note || '', r.cards_note || '', r.video_note || ''].filter(Boolean).join('. ');
ops.push({ stage: 2, method: 'POST', url: `${base}/items/${id}/status`, body: { status: 'draft', note: why.slice(0, 1500) } });
if (r.ok) ops.push({ stage: 3, method: 'POST', url: `${base}/items/${id}/status`, body: { status: 'in_review', note: 'passed the quality gate' } });
return rawOps(ops);
"""

DRAFT_RECORD = r"""
const a = $('Attach video').first().json;
const failed = ['Run stage 1', 'Run stage 2', 'Run stage 3'].flatMap(n => $(n).all())
  .filter(r => !r.json.noop && r.json.statusCode >= 300).map(r => JSON.stringify((r.json.body || {}).detail || r.json.body).slice(0, 160));
const slot = $input.first().json || {};
const reg = $('Register novelty').first().json || {};
const problems = [...failed, ...(slot.status === 'drafted' ? [] : ['slot not marked drafted in 61']),
  ...(reg.item_id ? [] : ['text not registered for the novelty check'])];
return [{ json: { result: failed.length ? 'error' : 'drafted', slot_id: a.slot_id, item_id: a.item_id, channel: a.channel,
  format: a.format, hook: a.hook, attempt: a.attempt, status: a.ok ? 'in_review' : 'draft', image: !!a.image_url,
  video: a.video_url || null, video_note: a.video_note || null, gate: a.problems, error: problems.join('; ') } }];
"""

DRAFT_SUMMARY = r"""
const rows = $input.all().map(i => i.json);
const notes = $('Pick slots').first().json.notes || [];
const drafted = rows.filter(r => r.result === 'drafted');
const review = drafted.filter(r => r.status === 'in_review').length;
const dropped = rows.filter(r => r.result === 'dropped');
const errors = rows.filter(r => r.result === 'error');
const form = `${String($env.N8N_PUBLIC_URL || 'http://localhost:5678/').replace(/\/?$/, '/')}form/mkt-content-approval`;
const line = r => r.result === 'drafted'
  ? `- #${r.item_id} ${r.channel} ${r.format} (${r.hook}${r.attempt === 2 ? ', 2nd try' : ''}) → ${r.status}` + (r.gate.length ? `: ${r.gate.join('; ').slice(0, 160)}` : '') + (r.error ? ` ⚠ ${r.error}` : '')
    + (r.video_note && !r.video ? ` (${r.video_note})` : '')
  : r.result === 'dropped' ? `- #${r.item_id} ${r.channel} dropped: ${r.reason.slice(0, 160)}`
  : `- slot #${r.slot_id} (${r.channel}): error, retried next run: ${r.error}`;
return [{ json: { drafted: drafted.length, dropped: dropped.length, errors: errors.length, text: [
  `*Content engine*: ${drafted.length} drafted (${review} in review, ${drafted.length - review} need a human), ` +
  `${dropped.length} dropped as near-duplicates, ${errors.length} error${errors.length === 1 ? '' : 's'}. Review them: ${form}`,
  ...rows.map(line), ...notes.map(n => `- ${n}`),
].join('\n') } }];
"""


def wf65():
    wf = Workflow(65, "Schedule · Engine drafter")
    t = schedule(wf, "Daily 06:00", "0 6 * * *")
    ap = http(wf, "Active pillars", "GET", "={{ $env.ENGINE_URL }}/pillars", query={"status": "active"},
              full_response=True, timeout=30000)
    ii = http(wf, "Idea items", "GET", "={{ $env.CALENDAR_URL }}/items", query={"status": "idea"}, key=True,
              full_response=True)
    bp = brand_profile(wf)
    sr = code(wf, "Slot requests", r"""
const raw = [].concat($('Active pillars').first().json.body ?? []);
const pillars = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(p => p && p.id && p.status === 'active');
if (!pillars.length) return [{ json: { url: `${$env.ENGINE_URL}/health` } }];
return pillars.map(p => ({ json: { url: `${$env.ENGINE_URL}/pillars/${p.id}/slots?status=planned` } }));
""")
    ps = http(wf, "Planned slots", "GET", "={{ $json.url }}", full_response=True, never_error=True, timeout=30000)
    pk = code(wf, "Pick slots", DRAFT_PICK)
    any_ = if_true(wf, "Anything to draft?", "={{ $json.pick }}")
    nothing = code(wf, "Nothing to draft", "return $input.all();   // no notification for a quiet day", pos=[wf._x, 620])
    loop = loop_one_by_one(wf, "Loop")
    ex = http(wf, "Examples", "GET", "={{ $env.LEARNING_URL }}/examples",
              query={"channel": "={{ $json.slot.channel }}", "k": "2", "by": "performance"},
              never_error=True, continue_on_fail=True, full_response=True, timeout=15000, pos=[wf._x, 520])
    rq = code(wf, "Request", DRAFT_REQUEST, pos=[wf._x + 240, 520])
    wr = http(wf, "Write", "POST", "={{ $env.GATEWAY_URL }}/v1/run", "={{ JSON.stringify($json.request) }}",
              key=True, llm=True, never_error=True, continue_on_fail=True, pos=[wf._x + 480, 520])
    dt = code(wf, "Draft text", DRAFT_TEXT, pos=[wf._x + 720, 520])
    nc = http(wf, "Novelty check", "POST", "={{ $env.ENGINE_URL }}/novelty/check",
              "={{ JSON.stringify({ text: $json.text || '(no draft)', channel: $json.channel, exclude_item_id: $json.item_id }) }}",
              never_error=True, continue_on_fail=True, timeout=120000, pos=[wf._x + 960, 520])
    jd = code(wf, "Judge", DRAFT_JUDGE, pos=[wf._x + 1200, 520])
    rt = if_true(wf, "Retry?", "={{ $json.route === 'retry' }}", pos=[wf._x + 1440, 520])
    kp = if_true(wf, "Keep?", "={{ $json.route === 'gate' }}", pos=[wf._x + 1680, 620])
    dp = if_true(wf, "Dropped?", "={{ $json.route === 'drop' }}", pos=[wf._x + 1920, 820])
    x0 = wf._x + 1920
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.gateText || $json.text }}", "channel": "={{ $json.gateChannel }}",
                                               "context": "={{ $json.facts }}", "ref": "={{ $json.item_id }}",
                                               "rewrite": "={{ $json.rewrite }}", "links": ""}, pos=[x0, 520])
    ag = code(wf, "After gate", DRAFT_AFTER_GATE, pos=[x0 + 240, 520])
    cr = code(wf, "Card request", CARD_REQUEST + r"""
const social = !['blog', 'email', 'video_script'].includes($json.format);
// A carousel gets one title card per slide (at most 8, one fast call each to 17); the first
// becomes the item's image_url, the others are listed in the body.
const slides = $json.format === 'carousel_text' && Array.isArray($json.slides) ? $json.slides.slice(0, 8) : [];
if (slides.length) return slides.map((t, i) => ({ json: { ...cardRequest($json.channel, t), post: $json, slide: i + 1 } }));
return [{ json: { ...cardRequest(social ? $json.channel : 'none', $json.core), post: $json } }];
""", pos=[x0 + 480, 520])
    wf._x = x0 + 720
    ic = image_card(wf)
    ai = code(wf, "Attach image", IMAGE_URL_OF + r"""
const reqs = $('Card request').all().map(i => i.json);
const res = $input.all().map(i => i.json);
const g = reqs[0].post;
const urls = reqs.map((q, i) => imageUrlOf(q, res[i]));
const image_url = urls[0] || null;
// Carousel: slide k's card goes in the item's notes (the body stays the publishable text).
// Fail-soft: a card that could not be made is named, the draft is kept.
const cards_note = reqs.length > 1 ? `slide cards (${urls.filter(Boolean).length} of ${urls.length} made): ` +
  urls.map((u, i) => `slide ${i + 1} ${u || 'not made'}`).join(', ') : null;
return [{ json: { ...g, cards_note, image_url, image_note: !image_url && g.channel === 'instagram' ? NO_IMAGE_IG : null } }];
""")
    # A video script gets a preview MP4 from 71 (VIDEO_URL); fail-soft, like the cards.
    vq = code(wf, "Video request", VIDEO_REQUEST + r"""
const g = $input.first().json;
return [{ json: videoRequest(g.format === 'video_script' ? g.script : null) }];
""")
    vr = video_render(wf)
    av = code(wf, "Attach video", VIDEO_RESULT + r"""
const g = $('Attach image').first().json;
const v = videoResult($('Video request').first().json, $input.first().json);
return [{ json: { ...g, video_url: v.video_url, video_note: v.video_note,
  image_url: g.image_url || v.poster_url } }];
""")
    so = code(wf, "Save ops", DRAFT_SAVE_OPS)
    stages = staged_ops(wf, "Save ops", 3, "$env.CALENDAR_URL")
    rn = http(wf, "Register novelty", "POST", "={{ $env.ENGINE_URL }}/novelty/register",
              "={{ JSON.stringify({ item_id: $('Attach image').first().json.item_id, channel: $('Attach image').first().json.channel, "
              "text: $('Attach image').first().json.core }) }}", key=True, never_error=True, continue_on_fail=True, timeout=120000)
    sd = http(wf, "Slot drafted", "POST", "={{ $env.ENGINE_URL }}/slots/{{ $('Attach image').first().json.slot_id }}/status",
              "={{ JSON.stringify({ status: 'drafted', calendar_item_id: $('Attach image').first().json.item_id }) }}",
              key=True, never_error=True, continue_on_fail=True)
    rd = code(wf, "Record drafted", DRAFT_RECORD)
    # Dropped: the slot is dropped with the reason; the idea item stays, with a note, for a person.
    ds = http(wf, "Drop slot", "POST", "={{ $env.ENGINE_URL }}/slots/{{ $json.slot_id }}/status",
              "={{ JSON.stringify({ status: 'dropped', reason: ('near-duplicate twice: ' + $json.reasons.join('; ')).slice(0, 1900) }) }}",
              key=True, never_error=True, continue_on_fail=True, pos=[x0 + 240, 820])
    dn = http(wf, "Note drop", "PATCH", "={{ $env.CALENDAR_URL }}/items/{{ $('Judge').first().json.item_id }}",
              "={{ JSON.stringify({ notes: [$('Loop').first(1).json.item.notes, 'engine: slot dropped, the draft was too close to an earlier post twice (' + $('Judge').first().json.reasons.join('; ').slice(0, 300) + ')'].filter(Boolean).join('\\n') }) }}",
              key=True, never_error=True, continue_on_fail=True, pos=[x0 + 480, 820])
    rdd = code(wf, "Record dropped", r"""
const d = $('Judge').first().json;
return [{ json: { result: 'dropped', slot_id: d.slot_id, item_id: d.item_id, channel: d.channel, format: d.format,
  reason: d.reasons.join('; ') || 'not novel' } }];
""", pos=[x0 + 720, 820])
    re_ = code(wf, "Record error", r"""
const d = $input.first().json;
return [{ json: { result: 'error', slot_id: d.slot_id, item_id: d.item_id, channel: d.channel, format: d.format,
  error: d.error || 'unexpected route' } }];   // the slot stays planned: retried next run
""", pos=[x0 + 240, 1020])
    sm = code(wf, "Summary", DRAFT_SUMMARY, pos=[wf._x, 100])
    gn = code(wf, "Webhook set?", GATE_NOTIFY, pos=[wf._x + 240, 100])
    n = notify(wf, "Notify", "$json.text", pos=[wf._x + 480, 100], waits="$json.drafted > 0")
    wf.chain(t, ap, ii, bp, sr, ps, pk, any_)
    wf.link(any_, loop, src_index=0)
    wf.link(any_, nothing, src_index=1)
    wf.link(loop, sm, src_index=0)      # done: one record per slot
    wf.link(loop, ex, src_index=1)      # one slot per round (the local LLM is slow)
    wf.chain(ex, rq, wr, dt, nc, jd, rt)
    wf.link(rt, rq, src_index=0)        # not novel on the first try: again with another hook
    wf.link(rt, kp, src_index=1)
    wf.link(kp, q, src_index=0)
    wf.link(kp, dp, src_index=1)
    wf.link(dp, ds, src_index=0)
    wf.link(dp, re_, src_index=1)
    wf.chain(q, ag, cr, ic, ai, vq, vr, av, so, *stages, rn, sd, rd)
    wf.chain(ds, dn, rdd)
    for r in (rd, rdd, re_):
        wf.link(r, loop)
    wf.chain(sm, gn, n)
    return wf, {
        "summary": "Every morning it drafts the content engine's next slots. It takes the `planned` slots of active pillars (61; a pillar paused by the stop rule is skipped) dated from today to `ENGINE_LOOKAHEAD_DAYS` ahead, earliest first, at most `ENGINE_DRAFTS_PER_RUN`, each with the `idea` calendar item that the plan tool (64) created. One slot at a time, it writes the piece from the slot's atom (and its evidence) with the slot's hook style and format through the gateway (03), using the same prompts as the writers: `social_posts` for posts (as 26 and 48 do, with `prefer_hooks` = the slot's hook and 2 examples from 46: approved posts that earned clearly more clicks on the slot's channel first, else the most recently approved), `x_thread` for X threads and `carousel_text` for carousels (same hook and examples; code numbers the thread's posts `1/N` and lays the carousel out as `Slide k — Title` blocks plus the caption), `blog_post` (25), `email_newsletter` (28) and `video_script` (57). Those writers save new calendar items, so they are not called as sub-workflows: this workflow fills the existing idea item instead. It then asks 61 whether the draft is too close to anything on that channel in the last 90 days (`/novelty/check`); if so it writes once more with a different hook, and if that is still too close the slot is dropped with the reason (the idea item keeps a note). A novel draft goes through the quality gate (35; report-only for video scripts, X threads and carousels; each part of an X thread is checked against 280 characters), gets a title card from 17 on image channels (a carousel gets one per slide: the first is the item's image, all are listed in the item's notes; a card that fails is named, the draft is kept), and a video script is rendered as a preview MP4 by 71 (`VIDEO_URL`, voice `VIDEO_VOICE`; saved as `video_url`, its poster as the image, with the note `video preview: <url> (<n> s)`, or `video not rendered: <reason>` when it fails: the draft is kept either way), and it is saved to its calendar item (title, body, hook style, image, video), which moves to `in_review` if the gate passed and stays `draft` with the problems otherwise. The final text is registered in 61 for later novelty checks and the slot is marked `drafted`. A failure on one slot leaves it `planned` for the next run. A summary with a link to the approval form goes to `NOTIFY_WEBHOOK_URL`. Nothing is published until a person approves it.",
        "schedule": "Daily 06:00",
        "env": {"ENGINE_URL": "the content engine (61)",
                "ENGINE_DRAFTS_PER_RUN": "most slots drafted per run; default 12 (each takes a few minutes on a local model)",
                "ENGINE_LOOKAHEAD_DAYS": "draft slots dated up to this many days ahead; default 7",
                "NOTIFY_WEBHOOK_URL": "optional; receives the summary", "N8N_PUBLIC_URL": "used for the approval-form link",
                "VIDEO_URL": "the video service (71); empty = video scripts get no preview video",
                "VIDEO_VOICE": "voice for the preview: `piper`, `say` (macOS only) or `none`; empty = 71's `TTS_BACKEND`"},
        "depends": ["61-content-engine", "03-llm-gateway", "19-content-calendar", "35-wf-tool-quality-gate",
                    "46-learning-service", "05-brand-service", "17-image-cards", "64-wf-tool-content-engine",
                    "71-video-assembly (optional)"],
    }


ALL_P4 = [wf64, wf65]
