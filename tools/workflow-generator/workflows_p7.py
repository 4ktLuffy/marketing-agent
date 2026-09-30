"""Phase 7: the control room's decision webhook (72-control-room/n8n/workflow.json).

The control room (72) is a web app; it never holds APPROVER_KEY. Its decisions go to this
webhook, which runs the SAME decision code as the approval form (38): DECISIONS_CORE and
decision_tail() from workflows.py. Only the input differs: JSON instead of form fields.
"""
from n8nlib import Workflow, code, http, if_true
from workflows import DECISIONS_CORE, VIDEO_SCRIPT_PARSE, decision_tail

# The workflow JSON ships inside the control room's repo, not as an NN-wf-* deploy.
PATH_72 = "72-control-room/n8n/workflow.json"

CHECK_REQUEST = r"""
// 1. The caller must send X-Control-Key = CONTROL_ROOM_KEY (constant-time compare). No key set
//    in n8n (or a short one) refuses everything.
// 2. Body: {reviewer, decisions: [{id, decision, text?, reason?, publish_at?, seen_sha256?}]}.
//    seen_sha256: the body_sha256 of the text shown on the card (64 hex characters, or empty).
const req = $input.first().json;
const want = String($env.CONTROL_ROOM_KEY || '');
const got = String((req.headers || {})['x-control-key'] || '');
let diff = want.length ^ got.length;
for (let i = 0; i < want.length; i++) diff |= want.charCodeAt(i) ^ (got.charCodeAt(i) || 0);
if (want.length < 16 || diff !== 0) return [{ json: { ok: false, status: 401, error: 'unauthorized' } }];
const b = req.body && typeof req.body === 'object' ? req.body : {};
const KINDS = ['approve', 'edit', 'reject_rewrite', 'reject_drop', 'back_to_draft', 'skip'];
const bad = why => [{ json: { ok: false, status: 422, error: why } }];
const str = (v, max) => v === undefined || v === null ? '' : typeof v === 'string' && v.length <= max ? v : null;
if (!Array.isArray(b.decisions) || !b.decisions.length || b.decisions.length > 50) return bad('decisions: 1 to 50 entries');
const reviewer = str(b.reviewer, 80);
if (reviewer === null) return bad('reviewer: text, at most 80 characters');
const decisions = [];
for (const [k, x] of b.decisions.entries()) {
  if (!x || typeof x !== 'object') return bad(`decisions[${k}]: an object`);
  if (!Number.isInteger(x.id) || x.id < 1) return bad(`decisions[${k}].id: a positive integer`);
  if (!KINDS.includes(x.decision)) return bad(`decisions[${k}].decision: one of ${KINDS.join(', ')}`);
  const text = str(x.text, 60000), reason = str(x.reason, 500), when = str(x.publish_at, 40);
  if (text === null || reason === null || when === null) return bad(`decisions[${k}]: text/reason/publish_at must be short text`);
  const seen = str(x.seen_sha256, 64);
  if (seen === null || (seen && !/^[0-9a-f]{64}$/.test(seen))) return bad(`decisions[${k}].seen_sha256: 64 hex characters (0-9a-f) or empty`);
  decisions.push({ id: x.id, decision: x.decision, text, reason, publish_at: when, seen_sha256: seen });
}
return [{ json: { ok: true, reviewer: reviewer.trim() || 'control room', decisions } }];
"""

REVIEW_ITEMS = r"""
// Decide only on items that are in_review right now (what the form lists), whatever the
// control room saw earlier. The others are reported back, nothing is changed for them.
const req = $('Check request').first().json;
const raw = [].concat($input.first().json.body ?? []);  // full response: the list is in body
const all = (raw.length === 1 && Array.isArray(raw[0]) ? raw[0] : raw).filter(i => i && i.id);
const wanted = [...new Set(req.decisions.filter(d => d.decision !== 'skip').map(d => String(d.id)))];
const items = all.filter(i => wanted.includes(String(i.id)));
const missing = wanted.filter(id => !items.some(i => String(i.id) === id)).map(Number);
return [{ json: { count: items.length, items, missing } }];
"""

# Turns the JSON decisions into the form's answers, so DECISIONS_CORE runs unchanged.
ANSWERS = r"""
const req = $('Check request').first().json;
const LABELS = { approve: 'Approve', edit: 'Edit & approve', reject_rewrite: 'Reject – rewrite it',
  reject_drop: 'Reject – drop it', back_to_draft: 'Back to draft', skip: 'Skip' };
const a = {};
for (const x of req.decisions) {   // an id sent twice: the last one wins, as in the form
  a[`Decision #${x.id}`] = LABELS[x.decision];
  a[`Text #${x.id}`] = x.text;
  a[`Reason #${x.id}`] = x.reason;
  a[`Publish at #${x.id}`] = x.publish_at;
  a[`Seen #${x.id}`] = x.seen_sha256;   // empty: DECISIONS_CORE falls back to the fetched body_sha256
}
const reviewer = req.reviewer;
const items = Object.fromEntries($('Review items').first().json.items.map(i => [String(i.id), i]));
"""

# `stale`: items 19 refused because their text changed after the card was shown; each has a
# "changed since you looked — reopen the card" line in `summary`.
RESULT = r"""
const s = $('Summary').first().json;
return [{ json: { ok: s.failed.length === 0 && s.stale.length === 0, summary: s.summary,
  not_in_review: $('Review items').first().json.missing, stale: s.stale, failed: s.failed, message: s.message } }];
"""


def respond(wf, name, body_expr, code_expr, pos=None):
    return wf.add(name, "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json", "responseBody": body_expr, "options": {"responseCode": code_expr},
    }, pos=pos)


def wf72():
    wf = Workflow(72, "Control room · Apply decisions")
    t = wf.add("Decisions webhook", "n8n-nodes-base.webhook", 2.1, {
        "httpMethod": "POST", "path": "mkt-apply-decisions", "responseMode": "responseNode", "options": {},
    }, webhookId="mkt-apply-decisions")
    ck = code(wf, "Check request", CHECK_REQUEST)
    ok = if_true(wf, "Allowed?", "={{ $json.ok }}")
    refuse = respond(wf, "Refuse", "={{ JSON.stringify({ ok: false, error: $json.error }) }}",
                     "={{ $json.status }}", pos=[wf._x, 460])
    l = http(wf, "Items in review", "GET", "={{ $env.CALENDAR_URL }}/items", key=True,
             query={"status": "in_review"}, full_response=True)
    ri = code(wf, "Review items", REVIEW_ITEMS)
    any_ = if_true(wf, "Anything to decide?", "={{ $json.count > 0 }}")
    none = respond(wf, "Nothing to decide", "={{ JSON.stringify({ ok: true, summary: [], not_in_review: $json.missing, "
                   "failed: [], message: 'No decisions made.' }) }}", 200, pos=[wf._x, 460])
    d = code(wf, "Decisions", VIDEO_SCRIPT_PARSE + ANSWERS + DECISIONS_CORE, pos=[wf._x + 240, 200])
    tail = decision_tail(wf, "Review items")
    res = code(wf, "Result", RESULT, pos=[wf._x + 2160, 200])
    out = respond(wf, "Respond", "={{ JSON.stringify($json) }}", 200, pos=[wf._x + 2400, 200])
    wf.chain(t, ck, ok)
    wf.link(ok, l, src_index=0)
    wf.link(ok, refuse, src_index=1)
    wf.chain(l, ri, any_)
    wf.link(any_, d, src_index=0)
    wf.link(any_, none, src_index=1)
    wf.chain(d, *tail, res, out)
    return wf, {}


EXTRA_P7 = [(wf72, PATH_72)]
