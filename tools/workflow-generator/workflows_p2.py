"""Phase 2 workflows (47-53): campaigns, learning from review, measurement."""
from n8nlib import (FORMS_CRED, OPS_TO_ITEMS, RAW_OPS, Workflow, call_workflow, code, dynamic_http, gateway, http,
                    if_true, notify, schedule, staged_ops, sub_trigger, GATE_NOTIFY)
from workflows import CARD_REQUEST, IMAGE_URL_OF, brand_profile, image_card

FAILED_OPS = r"""
function failedOps(runNodes) {
  return runNodes.flatMap(n => $(n).all()).filter(r => !r.json.noop && r.json.statusCode >= 300)
    .map(r => JSON.stringify((r.json.body || {}).detail || r.json.body));
}
"""


# --------------------------------------------------------------------------- 47 plan campaign

def wf47():
    wf = Workflow(47, "Tool · Plan a campaign")
    s = sub_trigger(wf, [(k, "string") for k in (
        "name", "goal", "channels", "start_date", "end_date", "audience", "offer", "kpis", "landing_url")])
    perf = http(wf, "Recent performance", "GET", "={{ $env.CAMPAIGNS_URL }}/insights",
                query={"days": "90"}, continue_on_fail=True)
    prep = code(wf, "Prepare", r"""
const s = $('Start').first().json;
const ins = $input.first().json;
const perf = (ins.top_posts || []).slice(0, 5).map(p => `- ${p.channel}: "${p.title}" (${p.clicks} clicks)`).join('\n');
const channels = String(s.channels || '').split(/,|;| and /).map(c => c.trim().toLowerCase()).filter(Boolean);
if (!s.name || !s.goal) throw new Error('A campaign needs at least a name and a goal');
if (!channels.length) throw new Error('Name the channels, e.g. "linkedin, email"');
const iso = /^\d{4}-\d{2}-\d{2}$/;
if (!iso.test(s.start_date || '') || !iso.test(s.end_date || '')) throw new Error('Give start and end dates as YYYY-MM-DD');
// Targets the user typed are parsed here, never by the model: asked for "50 clicks", the
// model once saved "signups >= 50".
const METRICS = { click: 'clicks', clicks: 'clicks', session: 'sessions', sessions: 'sessions', visit: 'sessions', visits: 'sessions',
  visitor: 'sessions', visitors: 'sessions', conversion: 'conversions', conversions: 'conversions', signup: 'signups', signups: 'signups',
  'sign-up': 'signups', 'sign-ups': 'signups', subscriber: 'signups', subscribers: 'signups', revenue: 'revenue', sales: 'revenue',
  ctr: 'ctr', cvr: 'cvr', 'open rate': 'open_rate', opens: 'open_rate' };
const parsed = [];
const text = String(s.kpis || '').toLowerCase();
for (const m of text.matchAll(/\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(%?)\s*([a-z][a-z -]*)/g)) {
  const word = m[3].trim().split(/\s+/).slice(0, 2).join(' ');
  const metric = METRICS[word] || METRICS[word.split(' ')[0]];
  if (!metric) continue;
  let value = Number(m[1].replace(/,/g, ''));
  if (m[2] === '%' || ['ctr', 'cvr', 'open_rate'].includes(metric)) value = value > 1 ? value / 100 : value;
  if (value > 0 && !parsed.some(k => k.metric === metric)) parsed.push({ metric, target_value: value });
}
if (s.kpis && s.kpis.trim() && !parsed.length) throw new Error(`Could not read the targets "${s.kpis}". Write them like "500 clicks, 20 signups".`);
return [{ json: { ...s, channels, performance: perf || null, user_kpis: (s.kpis || '').trim(), parsed_kpis: parsed } }];
""")
    plan = gateway(wf, "Plan", "campaign_plan",
                   "{ name: $json.name, goal: $json.goal, channels: $json.channels.join(', '), "
                   "start_date: $json.start_date, end_date: $json.end_date, audience: $json.audience || null, "
                   "offer: $json.offer || null, kpis: $json.user_kpis || null, performance: $json.performance }")
    create = http(wf, "Create campaign", "POST", "={{ $env.CAMPAIGNS_URL }}/campaigns",
                  "={{ JSON.stringify({ name: $('Prepare').first().json.name, goal_type: $json.output.goal_type, "
                  "goal_text: $('Prepare').first().json.goal, audience: $('Prepare').first().json.audience || 'Our usual audience (see the brand profile)', "
                  "offer: $('Prepare').first().json.offer || null, landing_url: $('Prepare').first().json.landing_url || null, "
                  "channels: $('Prepare').first().json.channels, start_date: $('Prepare').first().json.start_date, "
                  "end_date: $('Prepare').first().json.end_date, notes: 'Key messages: ' + $json.output.key_messages.join(' | '), "
                  "kpis: ($('Prepare').first().json.parsed_kpis.length ? $('Prepare').first().json.parsed_kpis : $json.output.kpis).map(k => ({ metric: k.metric, target_value: k.target_value })) }) }}",
                  key=True)
    items = code(wf, "Plan items", r"""
const p = $('Prepare').first().json;
const plan = $('Plan').first().json.output;
const c = $input.first().json;
if (!c.id) throw new Error(`Campaign not created: ${JSON.stringify(c.detail || c)}`);
// The model often renames channels ("Social Media (Twitter)" for x). Map what it means;
// pieces for channels nobody asked for are moved to the requested ones in rotation.
const ALIAS = { twitter: 'x', 'x (twitter)': 'x', ig: 'instagram', fb: 'facebook', newsletter: 'email', mail: 'email' };
function channelOf(raw) {
  const c = String(raw).trim().toLowerCase();
  if (p.channels.includes(c)) return c;
  if (ALIAS[c] && p.channels.includes(ALIAS[c])) return ALIAS[c];
  return p.channels.find(ch => c.includes(ch.length > 2 ? ch : '(' + ch + ')')
    || (ch === 'x' && c.includes('twitter')) || (ch === 'email' && c.includes('email'))) || null;
}
let rr = 0, moved = 0;
const out = plan.assets
  .filter(a => a.date >= p.start_date && a.date <= p.end_date)
  .map(a => { let ch = channelOf(a.channel); if (!ch) { ch = p.channels[rr++ % p.channels.length]; moved++; } return { ...a, channel: ch }; })
  .map(a => ({ json: {
    title: a.title.slice(0, 120), channel: a.channel, status: 'idea',
    body: `[${a.angle}] ${a.brief}\nKey messages: ${plan.key_messages.join(' | ')}`,
    scheduled_at: `${a.date}T09:00:00Z`, campaign_id: c.id, campaign: c.slug } }));
if (!out.length) throw new Error('The plan had no pieces inside the campaign dates');
out.forEach(o => { o.json.moved = moved; });
return out;
""")
    add = http(wf, "Add to calendar", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json) }}", key=True)
    one = code(wf, "Campaign id", r"""
return [{ json: { campaign_id: String($('Create campaign').first().json.id) } }];
""")
    draft = call_workflow(wf, "Start drafting", 48, {"campaign_id": "={{ $json.campaign_id }}"}, wait=False)
    reply = code(wf, "Reply", r"""
const c = $('Create campaign').first().json;
const plan = $('Plan').first().json.output;
const p = $('Prepare').first().json;
const items = $('Add to calendar').all().map(i => i.json);
const moved = $('Plan items').first().json.moved;
const kpis = c.kpis.map(k => `${k.metric} ≥ ${k.target_value}`).join(', ');
return [{ json: { result: [
  `Campaign #${c.id} "${c.name}" created (status: planned, utm_campaign: ${c.slug}).`,
  `Goal type: ${plan.goal_type}. Key messages: ${plan.key_messages.join(' · ')}`,
  p.user_kpis ? `Targets (yours): ${kpis}` : `PROPOSED targets: ${kpis}. They're only suggestions: confirm or change them, then activate the campaign.`,
  '', `${items.length} pieces planned${moved ? ` (${moved} were planned for other channels and moved to yours)` : ''}:`,
  ...items.map(i => `- #${i.id} ${i.scheduled_at.slice(0, 10)} ${i.channel}: ${i.title}`),
  '', 'Drafting has started in the background. Drafts appear in the approval form as they pass the quality gate.',
].join('\n') } }];
""")
    wf.chain(s, perf, prep, plan, create, items, add, one, draft, reply)
    return wf, {
        "summary": "Turns a campaign brief into a real campaign: goal type, key messages and targets (yours, or proposed ones marked as such), created in the campaign service (45) with a dated plan of pieces in the calendar. It then starts the drafter (48) in the background. The campaign stays `planned` until you confirm the targets and activate it.",
        "inputs": {"name": "campaign name", "goal": "what it should achieve", "channels": "e.g. `linkedin, email`",
                   "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", "audience": "optional", "offer": "optional",
                   "kpis": "optional targets, e.g. `20 signups, 500 clicks` (otherwise proposed)", "landing_url": "optional page the links point to"},
        "output": "`{result}`: campaign id, targets, planned pieces",
        "depends": ["03-llm-gateway", "45-campaign-service", "19-content-calendar", "48-wf-campaign-drafter"],
        "called_by": "the chat agent (24), tool `plan_campaign`",
        "test": {"name": "Team Box autumn push", "goal": "More distributed teams try the Team Box", "channels": "linkedin, email",
                 "start_date": "2026-10-05", "end_date": "2026-10-30", "audience": "Ops leads at remote startups",
                 "offer": "First month $79, free US shipping", "kpis": "20 signups", "landing_url": "https://northwind-roasters.example.com/team-box"},
    }

# --------------------------------------------------------------------------- 48 campaign drafter

def wf48():
    wf = Workflow(48, "Background · Campaign drafter")
    s = sub_trigger(wf, [("campaign_id", "string")])
    camp = http(wf, "Campaign", "GET", "={{ $env.CAMPAIGNS_URL }}/campaigns/{{ $json.campaign_id }}")
    bp = brand_profile(wf)
    ideas = http(wf, "Ideas", "GET", "={{ $env.CALENDAR_URL }}/items", key=True,
                 query={"campaign_id": "={{ $('Campaign').first().json.id }}", "status": "idea"}, full_response=True)
    split = code(wf, "One per idea", r"""
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const items = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(i => i && i.id && i.status === 'idea');
return items.map(i => ({ json: i }));
""")
    link = http(wf, "Campaign link", "POST", "={{ $env.CAMPAIGNS_URL }}/campaigns/{{ $('Campaign').first().json.id }}/link",
                "={{ JSON.stringify({ channel: $json.channel, content: String($json.id) }) }}", never_error=True)
    ex = http(wf, "Approved examples", "GET", "={{ $env.LEARNING_URL }}/examples",
              query={"channel": "={{ $('One per idea').item.json.channel }}", "k": "2", "by": "performance"},
              never_error=True, continue_on_fail=True, full_response=True)
    req = code(wf, "Build request", r"""
const it = $('One per idea').item.json;
const c = $('Campaign').first().json;
const link = $('Campaign link').item.json.url || null;
const exRaw = $('Approved examples').item.json.body;
const examples = (Array.isArray(exRaw) ? exRaw : []).map(e => `- ${e.text}`).join('\n') || null;
const brief = `${it.body}\nCampaign: ${c.name}. Goal: ${c.goal_text}.${c.offer ? ' Offer: ' + c.offer + '.' : ''}`;
// Only what the USER gave counts as evidence for the fact check. The piece's brief was
// written by the planner (an LLM) and once invented "50% off"; as evidence it would have
// approved its own invention.
const userFacts = [`Goal: ${c.goal_text}.`, c.offer ? `Offer: ${c.offer}.` : '', c.audience ? `Audience: ${c.audience}.` : ''].filter(Boolean).join('\n');
let request;
if (it.channel === 'blog') {
  request = { prompt: 'blog_post', vars: { topic: it.title, audience: c.audience || null, context: brief, length_words: 600 } };
} else if (it.channel === 'email') {
  request = { prompt: 'email_newsletter', vars: { topic: it.title, audience: c.audience || null, context: brief } };
} else {
  request = { prompt: 'social_posts', vars: { topic: `${it.title}. ${brief}`, channels: it.channel, link, campaign: c.slug, examples } };
}
return { json: { item_id: it.id, channel: it.channel, link, brief, userFacts, request } };
""", each=True)
    write = http(wf, "Write", "POST", "={{ $env.GATEWAY_URL }}/v1/run", "={{ JSON.stringify($json.request) }}", key=True, llm=True)
    text = code(wf, "Draft text", r"""
const b = $('Build request').item.json;
const out = $json.output;
let text;
if (b.channel === 'blog') text = out;
else if (b.channel === 'email') text = `Subject: ${out.subject}\nPreheader: ${out.preheader}\n\n${out.body_markdown}`;
else {
  const post = (out.posts || []).find(p => String(p.channel).trim().toLowerCase() === b.channel) || (out.posts || [])[0];
  if (!post) throw new Error(`No post for ${b.channel}`);
  text = post.text.trim();
  if (b.link && !text.includes(b.link)) text += `\n\n${b.link}`;
}
return { json: { ref: String(b.item_id), channel: b.channel, text, context: b.userFacts, links: b.link || '' } };
""", each=True)
    gate = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.text }}", "channel": "={{ $json.channel }}",
                                                  "context": "={{ $json.context }}", "ref": "={{ $json.ref }}",
                                                  "links": "={{ $json.links }}"})
    cr = code(wf, "Card request", CARD_REQUEST + r"""
const idea = $('One per idea').all().map(i => i.json).find(i => String(i.id) === String($json.ref)) || {};
return { json: { ...cardRequest($json.channel, idea.title || $json.text), post: $json } };
""", each=True)
    ic = image_card(wf)
    ai = code(wf, "Attach image", IMAGE_URL_OF + r"""
const req = $('Card request').item.json;
const g = req.post;
const image_url = imageUrlOf(req, $json);
return { json: { ...g, image_url, image_note: !image_url && g.channel === 'instagram' ? NO_IMAGE_IG : null } };
""", each=True)
    ops = code(wf, "Save ops", RAW_OPS + r"""
const base = $env.CALENDAR_URL;
const ops = [];
for (const r of $input.all().map(i => i.json)) {
  const id = r.ref;
  const patch = { body: r.text };
  if (r.image_url) patch.image_url = r.image_url;
  ops.push({ stage: 1, method: 'PATCH', url: `${base}/items/${id}`, body: patch });
  const note = r.ok ? 'drafted by campaign drafter' : `drafted; needs a human. Quality gate: ${r.problems.join('; ')}`;
  ops.push({ stage: 2, method: 'POST', url: `${base}/items/${id}/status`, body: { status: 'draft',
    note: r.image_note ? `${note}. ${r.image_note}` : note } });
  if (r.ok) ops.push({ stage: 3, method: 'POST', url: `${base}/items/${id}/status`, body: { status: 'in_review', note: 'passed the quality gate' } });
}
return rawOps(ops);
""")
    stages = staged_ops(wf, "Save ops", 3, "$env.CALENDAR_URL")
    summ = code(wf, "Summary", FAILED_OPS + r"""
const results = $('Quality gate').all().map(i => i.json);
const failed = failedOps(['Run stage 1', 'Run stage 2', 'Run stage 3']);
const c = $('Campaign').first().json;
const ok = results.filter(r => r.ok).length;
return [{ json: { ready: ok, text: `*Campaign "${c.name}"*: ${ok} of ${results.length} drafts ready in the approval form` +
  (results.length - ok ? `, ${results.length - ok} saved as drafts that need a human` : '') +
  (failed.length ? ` (${failed.length} calendar updates failed: ${failed.slice(0, 3).join('; ')})` : '') } }];
""")
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text", waits="$json.ready > 0")
    wf.chain(s, camp, bp, ideas, split, link, ex, req, write, text, gate, cr, ic, ai, ops, *stages, summ, gn, n)
    return wf, {
        "summary": "Drafts every `idea` piece of a campaign in the background. Social posts get the campaign's tracked link (utm_content = the calendar item id) and 2 of your approved posts as style examples (46: the ones that earned clearly more clicks on that channel first, else the most recently approved). Every draft goes through the quality gate and fact check (35). Drafts that pass go to `in_review`; the others stay `draft` with the problems noted. Social pieces for instagram, facebook, threads, linkedin and x get a title card from 17 (the piece's title, the brand name from 05, sized by channel) as their `image_url`; if the card fails the piece is saved without one (an Instagram piece gets a note, since Instagram needs an image).",
        "inputs": {"campaign_id": "campaign id from 45"},
        "output": "calendar items updated; a summary is posted to `NOTIFY_WEBHOOK_URL`",
        "depends": ["45-campaign-service", "19-content-calendar", "03-llm-gateway", "46-learning-service", "35-wf-tool-quality-gate", "05-brand-service", "17-image-cards"],
        "called_by": "47 plan campaign (without waiting)",
        "test": {"campaign_id": "1"},
    }

# --------------------------------------------------------------------------- 49 revise draft

def wf49():
    wf = Workflow(49, "Background · Revise a rejected draft")
    s = sub_trigger(wf, [("item_id", "string"), ("reason", "string")])
    valid = code(wf, "Valid", r"""
return $input.all().filter(i => /^\d+$/.test(String(i.json.item_id || '')));
""")
    item = http(wf, "Item", "GET", "={{ $env.CALENDAR_URL }}/items/{{ $json.item_id }}", key=True)
    att = http(wf, "Attempts", "GET", "={{ $env.LEARNING_URL }}/items/{{ $('Valid').item.json.item_id }}/attempts",
               never_error=True, continue_on_fail=True)
    dec = code(wf, "Decide", r"""
const MAX = 3;
const it = $('Item').item.json;
const n = $json.rejections ?? 1;
return { json: { id: it.id, channel: it.channel, text: it.body, reason: $('Valid').item.json.reason || 'Make it clearer and more on-brand.',
  attempt: n, give_up: n > MAX } };
""", each=True)
    go = code(wf, "Within limit", r"""
return $input.all().filter(i => !i.json.give_up);
""")
    rev = gateway(wf, "Revise", "revise_with_feedback",
                  "{ text: $json.text, reason: $json.reason, channel: $json.channel }")
    keep = code(wf, "Keep links", r"""
const d = $('Within limit').item.json;
let text = $json.output.text.trim();
// The model has swapped links before; put back any URL from the rejected draft.
for (const url of (d.text.match(/https?:\/\/\S+/g) || [])) if (!text.includes(url)) text += `\n\n${url}`;
// The revision keeps the original's links, so their domains are allowed.
return { json: { ref: String(d.id), channel: d.channel, text, context: null,
  links: (d.text.match(/https?:\/\/\S+/g) || []).join(' ') } };
""", each=True)
    gate = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.text }}", "channel": "={{ $json.channel }}",
                                                  "context": "", "ref": "={{ $json.ref }}", "links": "={{ $json.links }}"})
    ops = code(wf, "Save ops", RAW_OPS + r"""
const base = $env.CALENDAR_URL;
const attempts = Object.fromEntries($('Within limit').all().map(i => [String(i.json.id), i.json]));
const notesOf = Object.fromEntries($('Item').all().map(i => [String(i.json.id), i.json.notes || '']));
const ops = [];
for (const r of $input.all().map(i => i.json)) {
  const a = attempts[r.ref];
  ops.push({ stage: 1, method: 'PATCH', url: `${base}/items/${r.ref}`, body: { body: r.text } });
  if (r.ok) ops.push({ stage: 2, method: 'POST', url: `${base}/items/${r.ref}/status`,
    body: { status: 'in_review', note: `revised after feedback (attempt ${a.attempt}): ${a.reason}` } });
  else ops.push({ stage: 2, method: 'PATCH', url: `${base}/items/${r.ref}`, body: { notes:
    `${notesOf[r.ref]}\n[${new Date().toISOString().slice(0, 19)}Z] revision ${a.attempt} still has problems: ${r.problems.join('; ')}`.trim() } });
}
return rawOps(ops);
""")
    stages = staged_ops(wf, "Save ops", 2, "$env.CALENDAR_URL")
    wf.chain(s, valid, item, att, dec, go, rev, keep, gate, ops, *stages)
    return wf, {
        "summary": "Rewrites a rejected draft following the reviewer's reason, keeps its links, and runs it through the quality gate and fact check again. It goes back to `in_review` if it passes. After 3 rejections of the same item (counted by the learning service, 46) it stops and leaves the item as a draft for a human.",
        "inputs": {"item_id": "calendar item id (status must be `draft`)", "reason": "what the reviewer wants changed"},
        "output": "the calendar item is updated",
        "depends": ["19-content-calendar", "46-learning-service", "03-llm-gateway", "35-wf-tool-quality-gate"],
        "called_by": "38 approval form (without waiting), on *Reject – rewrite*",
        "test": {"item_id": "1", "reason": "Too salesy. Calmer, and say what it tastes like."},
    }

# --------------------------------------------------------------------------- 50 learning review

def wf50():
    wf = Workflow(50, "Schedule · Learn from reviews")
    t = schedule(wf, "Fridays 16:00", "0 16 * * 5")
    r = http(wf, "Reflect", "POST", "={{ $env.LEARNING_URL }}/reflect",
             "={{ JSON.stringify({ since_days: 7, max_events: 30 }) }}", key=True, timeout=1800000)
    c = code(wf, "New rules", r"""
const res = $input.first().json;
const rules = res.created || [];
if (!rules.length) return [];
const form = `${$env.N8N_PUBLIC_URL || ''}form/mkt-rules-review`;
return [{ json: { text: `*${rules.length} new writing rule(s) proposed from this week's reviews*\n` +
  rules.map(r => `- ${r.scope !== 'all' ? '[' + r.scope + '] ' : ''}${r.text}`).join('\n') +
  `\nKeep or reject them: ${form}` } }];
""")
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, r, c, gn, n)
    return wf, {
        "summary": "Every Friday it asks the learning service (46) to look at the week's edits and rejections and propose general writing rules (for example \"no rhetorical questions in openers\"). New proposals are posted to your webhook with a link to the rules form (51). Nothing is used until you keep it.",
        "schedule": "Fridays 16:00",
        "env": {"NOTIFY_WEBHOOK_URL": "optional", "N8N_PUBLIC_URL": "for the form link"},
        "depends": ["46-learning-service"],
    }

# --------------------------------------------------------------------------- 51 rules review form

def wf51():
    wf = Workflow(51, "Form · Review learned rules")
    t = wf.add("Open rules form", "n8n-nodes-base.formTrigger", 2.2, {
        "authentication": "basicAuth",
        "formTitle": "Rules learned from your reviews",
        "formDescription": "The agent proposed these rules from how you edited and rejected drafts. Rules you keep are added to every writing prompt.",
        "formFields": {"values": [{"fieldLabel": "Reviewer", "placeholder": "your name", "requiredField": True}]},
        "responseMode": "lastNode", "options": {},
    }, webhookId="mkt-rules-review", credentials=FORMS_CRED)
    # Pending rules from reviews, plus provisional rules from experiments that a later experiment replicated.
    l = http(wf, "Pending rules", "GET", "={{ $env.LEARNING_URL }}/rules/review", full_response=True)
    b = code(wf, "Build page", r"""
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const rules = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(r => r && r.id).slice(0, 15);
const fields = [];
for (const r of rules) {
  const ev = r.source === 'experiment' ? `<br><small>From experiments: ${r.support} agree (#${(r.source_experiment_ids || []).join(', #')}), ${r.contradicts} disagree. ` +
    (r.evidence || []).map(e => esc(e.summary || '')).filter(Boolean).slice(-2).join(' / ') + '</small>' : '';
  fields.push({ fieldType: 'html', html: `<p><b>${r.scope !== 'all' ? '[' + esc(r.scope) + '] ' : ''}${esc(r.text)}</b>${ev}</p>` });
  fields.push({ fieldLabel: `Rule #${r.id}`, fieldType: 'dropdown', requiredField: true,
    fieldOptions: { values: [{ option: 'Decide later' }, { option: 'Keep' }, { option: 'Reject' }] } });
}
return [{ json: { count: rules.length, fields } }];
""")
    i = if_true(wf, "Any pending?", "={{ $json.count > 0 }}")
    pg = wf.add("Rules page", "n8n-nodes-base.form", 2.3, {
        "operation": "page", "defineForm": "json", "jsonOutput": "={{ JSON.stringify($json.fields) }}",
        "options": {"formTitle": "Rules learned from your reviews", "buttonLabel": "Save"},
    }, pos=[wf._x, 200])
    ops = code(wf, "Decisions", OPS_TO_ITEMS + r"""
const base = $env.LEARNING_URL;
const map = { 'Keep': 'active', 'Reject': 'rejected' };
const ops = Object.entries($input.first().json)
  .map(([k, v]) => [k.match(/^Rule #(\d+)$/), v])
  .filter(([m, v]) => m && map[v])
  .map(([m, v]) => ({ method: 'POST', url: `${base}/rules/${m[1]}/status`, body: { status: map[v] }, label: `#${m[1]} ${v.toLowerCase()}` }));
return opsToItems(ops, base);
""", pos=[wf._x + 240, 200])
    run = dynamic_http(wf, "Save decisions", pos=[wf._x + 480, 200])
    sm = code(wf, "Summary", r"""
const res = $input.all();
const lines = res.filter(r => !r.json.noop).map((r, i) => {
  const op = $('Decisions').all()[i].json;
  return `${op.label}: ${r.json.statusCode < 300 ? 'saved' : 'failed'}`;
});
return [{ json: { message: lines.length ? lines.join('\n') : 'Nothing decided.' } }];
""", pos=[wf._x + 720, 200])
    done = wf.add("Done", "n8n-nodes-base.form", 2.3, {
        "operation": "completion", "respondWith": "text", "completionTitle": "Saved",
        "completionMessage": "={{ $json.message }}", "options": {},
    }, pos=[wf._x + 960, 200])
    empty = wf.add("Nothing pending", "n8n-nodes-base.form", 2.3, {
        "operation": "completion", "respondWith": "text", "completionTitle": "All clear",
        "completionMessage": "No proposed rules waiting.", "options": {},
    }, pos=[wf._x, 420])
    wf.chain(t, l, b, i)
    wf.link(i, pg, src_index=0)
    wf.link(i, empty, src_index=1)
    wf.chain(pg, ops, run, sm, done)
    return wf, {
        "summary": "A web form listing the writing rules the learning service proposed (46 `/rules/review`): rules reflected from your edits and rejections, and rules from experiments (\"prefer X over Y on C\") once a second, later experiment has agreed with the first (the form shows how many agree and disagree). Keep a rule and it is added to every writing prompt from then on (via the gateway, 03); reject it and it is never proposed again. A rule from a single experiment never reaches the writers.",
        "trigger": "n8n form at `<N8N_PUBLIC_URL>/form/mkt-rules-review`",
        "depends": ["46-learning-service"],
    }

# --------------------------------------------------------------------------- 52 campaign measurement

def wf52():
    wf = Workflow(52, "Schedule · Measure campaigns")
    t = schedule(wf, "Every day 06:00", "0 6 * * *")
    l = http(wf, "Campaigns", "GET", "={{ $env.CAMPAIGNS_URL }}/campaigns", full_response=True)
    ops = code(wf, "Measure ops", RAW_OPS + r"""
const base = $env.CAMPAIGNS_URL;
const today = new Date().toISOString().slice(0, 10);
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const all = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(c => c && c.id);
const ops = [];
for (const c of all.filter(c => ['active', 'paused'].includes(c.status))) {
  ops.push({ stage: 1, method: 'POST', url: `${base}/campaigns/${c.id}/measure`, body: {}, campaign: c.name });
  // A campaign past its end date is completed after its final measurement.
  if (c.end_date < today) ops.push({ stage: 2, method: 'POST', url: `${base}/campaigns/${c.id}/status`, body: { status: 'completed' }, campaign: c.name });
}
return rawOps(ops);
""")
    stages = staged_ops(wf, "Measure ops", 2, "$env.CAMPAIGNS_URL")
    unm = http(wf, "Never measured", "GET", "={{ $env.CAMPAIGNS_URL }}/report/unmeasured", continue_on_fail=True, full_response=True)
    rep = code(wf, "Report", r"""
const lines = [];
$('Run stage 1').all().forEach((r, i) => {
  const op = $('Stage 1').all()[i].json;
  if (op.noop) return;
  const b = r.json.body || {};
  const k = (b.kpis || []).map(x => `${x.metric} ${x.actual_value ?? '?'}/${x.target_value} (${x.state})`).join(', ');
  lines.push(`• ${op.campaign}: ${k || 'no KPIs'}${(b.errors || []).length ? ' ⚠ some sources unavailable' : ''}`);
});
$('Run stage 2').all().forEach((r, i) => {
  const op = $('Stage 2').all()[i].json;
  if (!op.noop && r.json.statusCode < 300) lines.push(`✔ "${op.campaign}" ended and is now completed.`);
});
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const unmeasured = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(c => c && c.id);
if (unmeasured.length) lines.push('', '*Shipped but never measured:*', ...unmeasured.map(c => `- ${c.name}: ${(c.unmeasured || []).join(', ')}`));
if (!lines.length) return [];
return [{ json: { text: `*Campaign scorecards*\n${lines.join('\n')}` } }];
""")
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, l, ops, *stages, unm, rep, gn, n)
    return wf, {
        "summary": "Every morning it measures each active campaign against its targets (clicks from the link shortener, visits and conversions from analytics), completes campaigns past their end date, and lists campaigns that ended with a target that was never measured.",
        "schedule": "06:00 daily",
        "env": {"NOTIFY_WEBHOOK_URL": "optional"},
        "depends": ["45-campaign-service"],
    }

# --------------------------------------------------------------------------- 53 campaigns tool

def wf53():
    wf = Workflow(53, "Tool · Campaigns")
    s = sub_trigger(wf, [("action", "string"), ("campaign", "string"), ("metric", "string"), ("target_value", "string")])
    l = http(wf, "All campaigns", "GET", "={{ $env.CAMPAIGNS_URL }}/campaigns", full_response=True)
    b = code(wf, "Build request", r"""
const s = $('Start').first().json;
const base = $env.CAMPAIGNS_URL;
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const all = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(c => c && c.id);
const action = String(s.action || 'list').trim().toLowerCase();
const noop = extra => [{ json: { method: 'GET', url: `${base}/health`, body: '{}', action, ...extra } }];
if (action === 'list') return noop({ list: all });
const q = String(s.campaign || '').trim().toLowerCase();
const match = all.find(c => String(c.id) === q.replace('#', '')) || all.find(c => c.slug === q)
  || all.find(c => c.name.toLowerCase() === q) || all.find(c => q && (c.name.toLowerCase().includes(q) || c.slug.includes(q.replace(/\s+/g, '-'))));
if (!match) return noop({ message: `No campaign matches "${s.campaign}". Campaigns: ${all.map(c => `#${c.id} ${c.name}`).join(', ') || 'none yet'}` });
const statuses = { activate: 'active', pause: 'paused', complete: 'completed', cancel: 'cancelled' };
if (action === 'scorecard') return [{ json: { method: 'POST', url: `${base}/campaigns/${match.id}/measure`, body: '{}', action, campaign: match } }];
if (statuses[action]) return [{ json: { method: 'POST', url: `${base}/campaigns/${match.id}/status`, body: JSON.stringify({ status: statuses[action] }), action, campaign: match } }];
if (action === 'set_target') {
  const metric = String(s.metric || '').trim().toLowerCase(); const value = Number(s.target_value);
  if (!metric || !(value > 0)) return noop({ message: 'set_target needs a metric (clicks, sessions, conversions, signups, revenue, ctr, cvr, open_rate) and a positive target_value' });
  const k = (match.kpis || []).find(k => k.metric === metric);
  return [{ json: k ? { method: 'PATCH', url: `${base}/campaigns/${match.id}/kpis/${k.id}`, body: JSON.stringify({ target_value: value }), action, campaign: match }
                  : { method: 'POST', url: `${base}/campaigns/${match.id}/kpis`, body: JSON.stringify({ metric, target_value: value }), action, campaign: match } }];
}
return noop({ message: `Unknown action "${action}". Use list, scorecard, activate, pause, complete, cancel or set_target.` });
""")
    call = dynamic_http(wf, "Call")
    f = code(wf, "Format", r"""
const b = $('Build request').first().json;
const r = $input.first().json;
if (b.message) return [{ json: { result: b.message } }];
if (b.list) return [{ json: { result: b.list.length ? b.list.map(c =>
  `#${c.id} ${c.name} [${c.status}] ${c.start_date}→${c.end_date} · targets: ${(c.kpis || []).map(k => `${k.metric} ${k.target_value}`).join(', ') || 'none'}`).join('\n')
  : 'No campaigns yet. Ask me to plan one.' } }];
const body = r.body || {};
if (r.statusCode >= 300) {
  const d = body.detail; return [{ json: { result: `The campaign service refused: ${typeof d === 'string' ? d : (d && d.message) || JSON.stringify(d)}` } }];
}
if (b.action === 'scorecard') return [{ json: { result: [`Scorecard for "${b.campaign.name}" (${Math.round(body.elapsed_pct ?? 0)}% of the campaign period elapsed):`,
  ...(body.kpis || []).map(k => `- ${k.metric}: ${k.actual_value ?? 'not measured'} / ${k.target_value} → ${k.state}`),
  `Pieces: ${Object.entries(body.assets || {}).map(([s, n]) => `${n} ${s}`).join(', ') || 'none'}`,
  ...((body.errors || []).length ? ['(some data sources were unavailable)'] : [])].join('\n') } }];
return [{ json: { result: `Done: "${b.campaign.name}" ${b.action === 'set_target' ? 'target saved' : 'is now ' + (body.status || b.action)}.` } }];
""")
    wf.chain(s, l, b, call, f)
    return wf, {
        "summary": "Lets the chat agent list campaigns, show a fresh scorecard, set targets, and activate, pause, complete or cancel a campaign (45). A campaign can't be activated without a target, and that rule is enforced by the service.",
        "inputs": {"action": "`list` · `scorecard` · `activate` · `pause` · `complete` · `cancel` · `set_target`",
                   "campaign": "campaign id, slug or name", "metric": "for set_target", "target_value": "for set_target"},
        "output": "`{result}`",
        "depends": ["45-campaign-service"],
        "called_by": "the chat agent (24), tool `campaigns`",
        "test": {"action": "list", "campaign": "", "metric": "", "target_value": ""},
    }


# --------------------------------------------------------------------------- 56 analytics sync

def wf56():
    wf = Workflow(56, "Schedule · Analytics sync (Umami)")
    t = schedule(wf, "Every day 05:30", "30 5 * * *")
    p = code(wf, "Yesterday", r"""
// Skip quietly when Umami isn't set up (UMAMI_SYNC_URL empty): CSV uploads still work.
if (!$env.UMAMI_SYNC_URL) return [];
const d = new Date(); d.setUTCDate(d.getUTCDate() - 1);
const day = d.toISOString().slice(0, 10);
return [{ json: { from: day, to: day } }];
""")
    s = http(wf, "Sync", "POST", "={{ $env.UMAMI_SYNC_URL }}/sync",
             "={{ JSON.stringify({ from: $json.from, to: $json.to }) }}", key=True, never_error=True,
             full_response=True, timeout=600000)
    r = code(wf, "Problems?", r"""
const res = $input.first().json;
const b = res.body || {};
if (res.statusCode < 300 && !(b.errors || []).length) return [];   // all good: stay quiet
const why = res.statusCode >= 300 ? JSON.stringify(b.detail || b) : (b.errors || []).map(e => JSON.stringify(e)).join('; ');
return [{ json: { text: `⚠ Umami analytics sync for ${$('Yesterday').first().json.from} had problems: ${why}` } }];
""")
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, p, s, r, gn, n)
    return wf, {
        "summary": "Every morning it pulls yesterday's visits and conversions per campaign and channel from Umami (through 55) into analytics (20), so campaign scorecards (52) and the weekly report (41) use real numbers without CSV uploads. It stays quiet on success and posts to your webhook if something failed.",
        "schedule": "05:30 daily, before campaign measurement (06:00)",
        "env": {"UMAMI_SYNC_URL": "`http://umami-sync:8000`; empty = skip (CSV uploads still work)", "NOTIFY_WEBHOOK_URL": "optional"},
        "depends": ["55-umami-sync"],
    }


ALL_P2 = [wf47, wf48, wf49, wf50, wf51, wf52, wf53, wf56]
