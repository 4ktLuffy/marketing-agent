"""Phase 8 workflows (74, 75, 76): the experiment loop; plus 41's "Experiments" section.

The agent proposes (LLM), a person approves (form 76, approver key), the content engine
(61) assigns the arms to planned slots, and the campaign service (45) decides in code at
weekly looks (HDI + ROPE). A winner becomes a provisional playbook rule in 46; only a later
replication plus a person (form 51) makes it active. Design: _dev/research/experiment-loop.md.
"""
from n8nlib import (FORMS_CRED, GATE_NOTIFY, OPS_TO_ITEMS, Workflow, code, dynamic_http, gateway, http, if_true,
                    notify, schedule)

OPT = dict(full_response=True, never_error=True, continue_on_fail=True, timeout=60000)
BODY_LIST = r"""
const listOf = n => { try { const raw = [].concat($(n).first().json.body ?? []);
  return (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(x => x && x.id); } catch (e) { return []; } };
"""
FORM_URL = "${String($env.N8N_PUBLIC_URL || 'http://localhost:5678/').trim().replace(/\\/?$/, '/')}"

# --------------------------------------------------------------------------- 74 experiment manager

MANAGER_DATA = BODY_LIST + r"""
// What the LLM may use: hook stats, clicks per channel, and every experiment so far (decided
// ones, no-difference ones included, so it does not re-test them). Built in code, compact.
const body = n => { try { const b = $(n).first().json.body; return b && typeof b === 'object' ? b : null; } catch (e) { return null; } };
const MAX_OPEN = Number($env.EXPERIMENT_MAX_PROPOSED || 2);
const channels = String($env.EXPERIMENT_CHANNELS || $env.PLAN_CHANNELS || 'linkedin, x').split(',').map(c => c.trim().toLowerCase()).filter(Boolean);
const exps = listOf('Experiments');
const open = exps.filter(e => ['proposed', 'approved', 'running'].includes(e.status));
const waiting = exps.filter(e => e.status === 'proposed');
const arms = e => (e.arms || []).map(a => a.value);
const data = {};
const hk = body('Hooks');
if (hk && Array.isArray(hk.styles)) {
  const used = hk.styles.filter(s => s.posts > 0).map(s => ({ hook_style: s.hook_style, posts: s.posts, clicks: s.clicks, clicks_per_post: s.clicks_per_post }));
  if (used.length) data.hooks = { days: 90, styles: used };
}
const ins = body('Clicks');
if (ins && (ins.by_channel || []).length) data.clicks = { days: 90, by_channel: ins.by_channel };
data.past = exps.filter(e => e.status === 'decided').map(e => ({ id: e.id, variable: e.variable, channels: e.channels,
  values: arms(e), decision: e.decision, winner: e.winner ? e.winner.value : null }));
data.running = open.map(e => ({ id: e.id, variable: e.variable, channels: e.channels, values: arms(e), status: e.status }));
const VARIABLES = 'hook_style: question, fact_led, story, how_to, benefit, contrarian; format: post, thread, carousel_text; ' +
  'cta: short free text (the call to action); length: short or long; time: HH:MM in UTC';
if (waiting.length >= MAX_OPEN)
  return [{ json: { go: false, text: `*Experiments*: ${waiting.length} proposals still wait for a person, none added. Review them: ${FORM}form/mkt-experiments` } }];
if (!data.hooks && !data.clicks)
  return [{ json: { go: false, text: '*Experiments*: no click data yet (45 /insights), nothing proposed.' } }];
return [{ json: { go: true, data: JSON.stringify(data), channels: channels.join(', '), variables: VARIABLES,
  room: MAX_OPEN - waiting.length } }];
""".replace("${FORM}", FORM_URL)

MANAGER_CHECK = OPS_TO_ITEMS + r"""
// The model proposes; code decides what is saved. Dropped: unknown variable or channel,
// invalid or equal values, evidence without a number from the data, a repeat.
const d = $('Data').first().json;
const g = $input.first().json || {};
const props = (((g.output || {}).proposals) || []).slice(0, Math.max(0, d.room));
const channels = d.channels.split(', ');
const HOOKS = ['question', 'fact_led', 'story', 'how_to', 'benefit', 'contrarian'];
const FORMATS = ['post', 'thread', 'carousel_text'];
const nums = s => (String(s).replace(/(\d),(?=\d{3}\b)/g, '$1').match(/\d+(?:\.\d+)?/g) || []).map(Number);
const known = new Set(nums(d.data));
const data = JSON.parse(d.data);
const key = (v, ch, a, b) => `${v}|${ch}|${[a, b].map(x => String(x).toLowerCase()).sort().join('/')}`;
const taken = new Set([...data.running, ...data.past.filter(p => p.decision === 'no_practical_difference')]
  .flatMap(e => e.channels.map(ch => key(e.variable, ch, e.values[0], e.values[1]))));
const valid = (v, x) => v === 'hook_style' ? HOOKS.includes(x) : v === 'format' ? FORMATS.includes(x)
  : v === 'time' ? /^([01]\d|2[0-3]):[0-5]\d$/.test(x) : v === 'length' ? ['short', 'long'].includes(x) : x.length >= 2;
const ops = [], dropped = [];
for (const p of props) {
  const v = String(p.variable || '').trim().toLowerCase(), ch = String(p.channel || '').trim().toLowerCase();
  const norm = x => ['hook_style', 'format', 'length'].includes(v) ? String(x || '').trim().toLowerCase().replace(/[ -]/g, '_') : String(x || '').trim();
  const a = norm(p.arm_a), b = norm(p.arm_b);
  const why = !channels.includes(ch) ? `channel "${ch}" is not one of ${channels.join(', ')}`
    : !valid(v, a) || !valid(v, b) ? `"${a}" / "${b}" are not valid ${v} values`
    : a.toLowerCase() === b.toLowerCase() ? 'both arms are the same'
    : !nums(p.evidence).length ? 'evidence cites no number'
    : !nums(p.evidence).every(x => known.has(x)) ? `evidence number not in the data (${nums(p.evidence).filter(x => !known.has(x)).join(', ')})`
    : taken.has(key(v, ch, a, b)) ? 'already running, proposed, or answered (no practical difference)' : '';
  if (why) { dropped.push(`${v} ${a} vs ${b} on ${ch}: ${why}`); continue; }
  taken.add(key(v, ch, a, b));
  ops.push({ method: 'POST', url: `${$env.CAMPAIGNS_URL}/experiments`, label: `${v}: ${a} vs ${b} on ${ch}`,
    evidence: String(p.evidence).slice(0, 300), body: { hypothesis: String(p.hypothesis).slice(0, 1000), variable: v, channels: [ch],
      arms: [{ value: a, brief: p.brief_a || null }, { value: b, brief: p.brief_b || null }], created_by: 'agent' } });
}
const note = g.output ? '' : `the model gave no proposals (${String((g.error && (g.error.message || g.error)) || 'no output').slice(0, 200)})`;
return opsToItems(ops, $env.CAMPAIGNS_URL).map(i => ({ json: { ...i.json, dropped, note } }));
"""

MANAGER_SUMMARY = r"""
const ops = $('Check proposals').all().map(i => i.json);
const res = $input.all().map(i => i.json);
const saved = [], refused = [];
res.forEach((r, i) => {
  const op = ops[i];
  if (op.noop) return;
  if (r.statusCode === 201) saved.push(`- #${r.body.id} ${op.label}. ${r.body.hypothesis} _(evidence: ${op.evidence})_`);
  else refused.push(`- ${op.label}: ${JSON.stringify((r.body || {}).detail ?? r.body).slice(0, 160)}`);
});
const { dropped, note } = ops[0];
const form = `${FORM}form/mkt-experiments`;
const lines = [`*Experiments*: ${saved.length} new proposal${saved.length === 1 ? '' : 's'}` + (saved.length ? ` waiting for your approval: ${form}` : '.')];
lines.push(...saved);
if (refused.length) lines.push('Not saved:', ...refused);
if (dropped.length) lines.push('Dropped in code:', ...dropped.map(x => `- ${x}`));
if (note) lines.push(note);
lines.push('Each runs until both versions have enough posts (12 each by default), at most 8 weeks; the numbers decide, weekly, never before.');
return [{ json: { text: lines.join('\n'), saved: saved.length } }];
""".replace("${FORM}", FORM_URL)


def wf74():
    wf = Workflow(74, "Schedule · Experiment manager")
    t = schedule(wf, "Mondays 07:00", "0 7 * * 1")
    hk = http(wf, "Hooks", "GET", "={{ $env.CAMPAIGNS_URL }}/insights/hooks",
              query={"days": "90", "explore": "0", "seed": "1"}, **OPT)
    cl = http(wf, "Clicks", "GET", "={{ $env.CAMPAIGNS_URL }}/insights", query={"days": "90"}, **OPT)
    xl = http(wf, "Experiments", "GET", "={{ $env.CAMPAIGNS_URL }}/experiments", **OPT)
    dt = code(wf, "Data", MANAGER_DATA)
    go = if_true(wf, "Propose?", "={{ $json.go }}")
    g = gateway(wf, "Propose", "experiment_proposals",
                "{ data: $json.data, channels: $json.channels, variables: $json.variables }", continue_on_fail=True)
    ck = code(wf, "Check proposals", MANAGER_CHECK)
    sv = dynamic_http(wf, "Save proposals")
    sm = code(wf, "Summary", MANAGER_SUMMARY)
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, hk, cl, xl, dt, go)
    wf.link(go, g, src_index=0)
    wf.link(go, gn, src_index=1)   # proposals still waiting, or no data: say so
    wf.chain(g, ck, sv, sm, gn, n)
    return wf, {
        "summary": "Every Monday the agent proposes at most 2 marketing experiments and saves them as `proposed` in the campaign service (45) for a person to approve in the experiments form (76); your webhook gets the approval link. The LLM (prompt `experiment_proposals`) sees the hook styles' clicks per post (45 `/insights/hooks`), clicks per channel (45 `/insights`), and every earlier experiment, including the ones that found **no practical difference** so it does not re-test them (45 also refuses those with 409). Code keeps a proposal only if its variable, channel and two values are valid, the arms differ, it is not already running or answered, and its `evidence` cites only numbers that appear in the data. An experiment is ONE variable (hook_style, format, cta, length or time), two arms, one channel, measured as clicks per post within 72 h of publishing. It stops early when 2 proposals already wait for a person (`EXPERIMENT_MAX_PROPOSED`). The model never decides a result: 45 does, in code (workflow 75).",
        "schedule": "Mondays 07:00",
        "env": {"EXPERIMENT_CHANNELS": "channels experiments may use (default `PLAN_CHANNELS`, else `linkedin, x`)",
                "EXPERIMENT_MAX_PROPOSED": "stop proposing while this many wait for approval (default 2)",
                "NOTIFY_WEBHOOK_URL": "optional; gets the proposals and the form link", "N8N_PUBLIC_URL": "for the form link"},
        "depends": ["45-campaign-service", "03-llm-gateway", "04-prompt-library (prompt `experiment_proposals`)",
                    "76-wf-experiments-form"],
    }


# --------------------------------------------------------------------------- 75 experiment analysis

ANALYSIS_EACH = BODY_LIST + r"""
const list = listOf('Running experiments');
return list.length ? list.map(e => ({ json: { id: e.id, url: `${$env.CAMPAIGNS_URL}/experiments/${e.id}/decide` } }))
                   : [{ json: { url: `${$env.CAMPAIGNS_URL}/health`, noop: true } }];
"""

ANALYSIS_RULES = OPS_TO_ITEMS + r"""
// A decided winner or no-difference result goes to the playbook (46): a winner starts a
// PROVISIONAL rule, a later same-direction winner marks it replicated (a person then approves
// it in form 51), a contradiction demotes or retires it. Inconclusive changes nothing.
const asked = $('One per experiment').all().map(i => i.json);
const ops = [];
$input.all().forEach((r, i) => {
  const b = r.json.body || {};
  if (asked[i].noop || r.json.statusCode !== 200 || b.status !== 'decided') return;
  if (!['winner', 'no_practical_difference'].includes(b.decision)) return;
  const e = b.experiment || {};
  ops.push({ method: 'POST', url: `${$env.LEARNING_URL}/rules/from-experiment`, experiment_id: e.id, body: {
    experiment_id: e.id, decision: b.decision, variable: e.variable, channels: e.channels,
    values: (e.arms || []).map(a => a.value), winner: b.winner_value || null, loser: b.loser_value || null,
    lift_hdi: b.lift_hdi, decided_at: e.decided_at, summary: b.summary || null } });
});
return opsToItems(ops, $env.LEARNING_URL);
"""

ANALYSIS_SUMMARY = r"""
const asked = $('One per experiment').all().map(i => i.json);
const decided = $('Decide').all().map(i => i.json);
const ruleOps = $('Rule ops').all().map(i => i.json);
const ruleRes = $input.all().map(i => i.json);
const rules = {};
ruleRes.forEach((r, i) => { if (!ruleOps[i].noop) rules[ruleOps[i].experiment_id] = r.statusCode === 200 ? r.body : { error: r.statusCode }; });
const lines = [], out = [];
decided.forEach((r, i) => {
  const a = asked[i];
  if (a.noop) return;
  const b = r.body || {};
  if (r.statusCode === 409) { out.push({ id: a.id, decision: 'not_due' });
    lines.push(`- #${a.id}: no look due (next ${((b.detail || {}).next_look_at || '?').slice(0, 10)})`); return; }
  if (r.statusCode !== 200) { out.push({ id: a.id, error: r.statusCode });
    lines.push(`- #${a.id}: could not be analysed (${r.statusCode}: ${JSON.stringify(b.detail ?? b).slice(0, 120)})`); return; }
  out.push({ id: a.id, look: b.look, decision: b.decision, lift_hdi: b.lift_hdi, rule: rules[a.id] || null });
  let line = `- #${a.id} look ${b.look}: ${b.summary}`;
  const rr = rules[a.id];
  if (rr && rr.action) {
    const act = { created: 'new PROVISIONAL rule (writers do not see it until a later experiment agrees and you approve it)',
      replicated: `rule replicated: approve it in the rules form ${FORM}form/mkt-rules-review`,
      confirmed: 'active rule confirmed again', demoted: 'active rule DEMOTED to provisional', retired: 'rule retired',
      contradicted: 'rule contradicted', counted: 'counted (rule was rejected)', already_counted: 'already counted', none: 'no rule' }[rr.action] || rr.action;
    line += ` → ${act}${rr.rule ? ` (#${rr.rule.id} "${rr.rule.text}")` : ''}`;
  } else if (rr && rr.error) line += ` → playbook update failed (${rr.error})`;
  lines.push(line);
});
const text = lines.length ? `*Experiments, weekly look*\n${lines.join('\n')}` : '*Experiments, weekly look*: none running.';
return [{ json: { text, results: out } }];
""".replace("${FORM}", FORM_URL)


def wf75():
    wf = Workflow(75, "Schedule · Experiment analysis")
    t = schedule(wf, "Mondays 08:30", "30 8 * * 1")
    rl = http(wf, "Running experiments", "GET", "={{ $env.CAMPAIGNS_URL }}/experiments", query={"status": "running"},
              full_response=True)
    each = code(wf, "One per experiment", ANALYSIS_EACH)
    dc = http(wf, "Decide", "={{ $json.noop ? 'GET' : 'POST' }}", "={{ $json.url }}", key=True,
              never_error=True, full_response=True, continue_on_fail=True, timeout=120000)
    ro = code(wf, "Rule ops", ANALYSIS_RULES)
    ru = dynamic_http(wf, "Update playbook")
    sm = code(wf, "Summary", ANALYSIS_SUMMARY)
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, rl, each, dc, ro, ru, sm, gn, n)
    return wf, {
        "summary": "Every Monday at 08:30 (before the weekly report) it asks the campaign service (45) to record the weekly look of every running experiment (`POST /experiments/{id}/decide`). 45 decides in code: per arm a Gamma-Poisson posterior of clicks per post (72 h after publishing, from the link shortener 16) with a prior at the pooled channel rate and a dispersion correction, the 95% HDI of the relative lift, and the HDI + ROPE rule (±15%): `winner`, `no_practical_difference`, or `inconclusive` at the last look (`max_weeks`); otherwise it waits for the next look. A look that is not due yet is refused (409) and skipped here: results are never peeked at early. A winner or a no-difference result goes to the learning service (46, `/rules/from-experiment`): a winner starts a **provisional** rule that writers do not see; a later experiment in the same direction marks it replicated, and only a person makes it active in the rules form (51); a contradicting result demotes or retires it. The summary (with the rules form link when a rule waits) goes to your webhook.",
        "schedule": "Mondays 08:30",
        "env": {"NOTIFY_WEBHOOK_URL": "optional; gets the weekly look summary", "N8N_PUBLIC_URL": "for the rules form link"},
        "depends": ["45-campaign-service", "46-learning-service", "16-link-shortener (through 45)"],
    }


# --------------------------------------------------------------------------- 76 experiments approval form

FORM_PAGE = BODY_LIST + r"""
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const exps = listOf('Proposed').slice(0, 10);
const fields = [];
for (const e of exps) {
  const arms = (e.arms || []).map(a => `<li><b>${esc(a.label)}</b>: ${esc(a.value)}${a.brief ? ` <i>(${esc(a.brief)})</i>` : ''}</li>`).join('');
  fields.push({ fieldType: 'html', html: `<p><b>#${e.id} · ${esc(e.variable)} on ${esc(e.channels.join(', '))}</b>` +
    ` <small>(proposed by ${esc(e.created_by)})</small></p><p>${esc(e.hypothesis)}</p><ul>${arms}</ul>` +
    `<p><small>Metric: clicks per post within 72 h of publishing. Stops when both versions have ${e.min_posts_per_arm} posts ` +
    `and the 95% interval of the lift clears ±${Math.round(e.rope * 100)}%, or after ${e.max_weeks} weeks; checked weekly. ` +
    `Every post is still approved one by one.</small></p>` });
  fields.push({ fieldLabel: `Experiment #${e.id}`, fieldType: 'dropdown', requiredField: true,
    fieldOptions: { values: [{ option: 'Decide later' }, { option: 'Approve' }, { option: 'Reject' }] } });
}
return [{ json: { count: exps.length, fields } }];
"""

FORM_DECISIONS = OPS_TO_ITEMS + r"""
const base = $env.CAMPAIGNS_URL;
const f = $input.first().json;
const who = String($('Open experiments form').first().json['Reviewer'] || 'reviewer').slice(0, 100);
const map = { 'Approve': 'approved', 'Reject': 'stopped' };
const ops = Object.entries(f).map(([k, v]) => [k.match(/^Experiment #(\d+)$/), v]).filter(([m, v]) => m && map[v])
  .map(([m, v]) => ({ method: 'POST', url: `${base}/experiments/${m[1]}/status`, label: `#${m[1]} ${v.toLowerCase()}d`,
    body: { status: map[v], by: who, reason: v === 'Reject' ? `rejected in the form by ${who}` : null } }));
return opsToItems(ops, base);
"""


def wf76():
    wf = Workflow(76, "Form · Review experiments")
    t = wf.add("Open experiments form", "n8n-nodes-base.formTrigger", 2.2, {
        "authentication": "basicAuth",
        "formTitle": "Experiments proposed by the agent",
        "formDescription": "Approve an experiment and the content engine starts giving its two versions to planned posts. Nothing is published without your approval of each post.",
        "formFields": {"values": [{"fieldLabel": "Reviewer", "placeholder": "your name", "requiredField": True}]},
        "responseMode": "lastNode", "options": {},
    }, webhookId="mkt-experiments", credentials=FORMS_CRED)
    l = http(wf, "Proposed", "GET", "={{ $env.CAMPAIGNS_URL }}/experiments", query={"status": "proposed"},
             full_response=True)
    b = code(wf, "Build page", FORM_PAGE)
    i = if_true(wf, "Any proposed?", "={{ $json.count > 0 }}")
    pg = wf.add("Experiments page", "n8n-nodes-base.form", 2.3, {
        "operation": "page", "defineForm": "json", "jsonOutput": "={{ JSON.stringify($json.fields) }}",
        "options": {"formTitle": "Experiments proposed by the agent", "buttonLabel": "Save"},
    }, pos=[wf._x, 200])
    ops = code(wf, "Decisions", FORM_DECISIONS, pos=[wf._x + 240, 200])
    # Approving needs the approver key (45 checks it like the calendar, 19): only n8n holds it.
    run = dynamic_http(wf, "Save decisions", pos=[wf._x + 480, 200], approver=True)
    sm = code(wf, "Summary", r"""
const ops = $('Decisions').all().map(i => i.json);
const lines = $input.all().map((r, i) => ops[i].noop ? null : `${ops[i].label}: ${r.json.statusCode < 300 ? 'saved' :
  'failed (' + JSON.stringify((r.json.body || {}).detail ?? r.json.body).slice(0, 120) + ')'}`).filter(Boolean);
return [{ json: { message: lines.length ? lines.join('\n') : 'Nothing decided.' } }];
""", pos=[wf._x + 720, 200])
    done = wf.add("Done", "n8n-nodes-base.form", 2.3, {
        "operation": "completion", "respondWith": "text", "completionTitle": "Saved",
        "completionMessage": "={{ $json.message }}", "options": {},
    }, pos=[wf._x + 960, 200])
    empty = wf.add("Nothing proposed", "n8n-nodes-base.form", 2.3, {
        "operation": "completion", "respondWith": "text", "completionTitle": "All clear",
        "completionMessage": "No experiments waiting for approval.", "options": {},
    }, pos=[wf._x, 420])
    wf.chain(t, l, b, i)
    wf.link(i, pg, src_index=0)
    wf.link(i, empty, src_index=1)
    wf.chain(pg, ops, run, sm, done)
    return wf, {
        "summary": "A web form listing the experiments the agent (74) or a person proposed, each with its hypothesis, the two versions, and the stopping rule stated up front. **Approve** moves it to `approved` in the campaign service (45; the call carries the approver key, which only n8n holds); the next plan of the content engine (61) then assigns the two versions to planned slots, balanced by weekday and hour, and the experiment runs. **Reject** stops it. Every post of an experiment still goes through the approval form (38).",
        "trigger": "n8n form at `<N8N_PUBLIC_URL>/form/mkt-experiments`",
        "depends": ["45-campaign-service"],
    }


# --------------------------------------------------------------------------- 41 experiments section (used in workflows.wf41)

WEEKLY_EXPERIMENTS = BODY_LIST + r"""
// Appended to the next-actions markdown: running, decided and no-difference experiments.
const md = $('Check actions').first().json.markdown || '';
const exps = listOf('Experiments');
if (!exps.length) return [{ json: { ...$('Check actions').first().json, markdown: md } }];
const arms = e => (e.arms || []).map(a => `${a.label} ${a.value}`).join(' vs ');
const run = exps.filter(e => e.status === 'running');
const dec = exps.filter(e => e.status === 'decided').sort((a, b) => String(b.decided_at).localeCompare(String(a.decided_at))).slice(0, 8);
const wait = exps.filter(e => ['proposed', 'approved'].includes(e.status));
const lines = ['### Experiments'];
if (run.length) lines.push('Running:', '', ...run.map(e => `- #${e.id} ${e.variable} on ${e.channels.join(', ')}: ${arms(e)}; ` +
  `${e.assigned.A} + ${e.assigned.B} posts assigned, next look ${String(e.next_look_at || '?').slice(0, 10)}`), '');
const verdict = e => e.decision === 'winner' ? `**${e.winner.value}** beat ${e.loser.value}`
  : e.decision === 'no_practical_difference' ? 'no practical difference: the choice is free' : 'inconclusive';
const winners = dec.filter(e => e.decision === 'winner'), nodiff = dec.filter(e => e.decision !== 'winner');
if (winners.length) lines.push('Decided:', '', ...winners.map(e => `- #${e.id} ${e.variable} on ${e.channels.join(', ')}: ${verdict(e)} (${String(e.decided_at).slice(0, 10)}; a rule needs a second experiment that agrees)`), '');
if (nodiff.length) lines.push('No difference or inconclusive:', '', ...nodiff.map(e => `- #${e.id} ${e.variable} on ${e.channels.join(', ')}, ${arms(e)}: ${verdict(e)} (${String(e.decided_at).slice(0, 10)})`), '');
if (wait.length) lines.push(`${wait.length} proposed or approved, not started yet.`);
return [{ json: { ...$('Check actions').first().json, markdown: [md, lines.join('\n').trim()].filter(Boolean).join('\n\n') } }];
"""


ALL_P8 = [wf74, wf75, wf76]
