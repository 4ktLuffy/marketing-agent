"""Lifecycle email flows (86-flow-runner): the 15-minute workflow shipped as 86-flow-runner/n8n/workflow.json.

86 does the work: it carries review decisions over from the calendar (19) and sends the steps
that are due (dry run by default: a row in its outbox). This workflow calls both endpoints, the
sync with the approver key that only n8n holds, and notifies the owner only when something
was sent, a flow was approved or rejected, a cap or pause was hit, or something failed.
Contract: 86-flow-runner/README.md.
"""
from n8nlib import NOTIFY_ON, Workflow, code, http, if_true, notify, schedule

# The workflow JSON ships inside the service's repo, not as an NN-wf-* deploy.
PATH_86 = "86-flow-runner/n8n/workflow.json"
FLOWS = "$env.FLOW_URL.replace(/\\/+$/, '')"

HELPERS = r"""
const ok = r => r && r.statusCode >= 200 && r.statusCode < 300;
const bodyOf = r => (r && r.body && typeof r.body === 'object' ? r.body : {});
const errText = r => { r = r || {}; const b = r.body; const e = (b && typeof b === 'object' ? (b.detail ?? b.message) : b) ?? r.error?.message ?? r.error; return e == null || e === '' ? 'no answer' : String(typeof e === 'object' ? JSON.stringify(e) : e).slice(0, 300); };
"""

FLOW_READY = HELPERS + r"""
const r = $input.first().json;
if (!$env.FLOW_URL) return [{ json: { go: false, notify: false, text: '*Email flows*: FLOW_URL is not set. Nothing to do.' } }];
if (!ok(r)) return [{ json: { go: false, notify: true, text: `*Email flows*: flow-runner (86) did not answer (${r.statusCode || 'no response'}: ${errText(r)}).` } }];
return [{ json: { go: true } }];
"""

# Pure: tests/flow_runner_test.js runs it. Quiet when nothing happened (the usual case).
FLOW_BUILD = HELPERS + r"""
const s = $('Sync reviews').first().json, t = $('Tick').first().json;
const sb = bodyOf(s), tb = bodyOf(t);
const lines = [], problems = [];
const list = a => (Array.isArray(a) ? a : []);
if (!ok(s)) problems.push(`review sync failed (${s.statusCode || 'no response'}: ${errText(s)})`);
else {
  if (list(sb.created).length) lines.push(`*Email flow waiting for approval*: ${list(sb.created).map(c => c.version).join(', ')} (the whole sequence is one item in the calendar).`);
  if (list(sb.approved).length) lines.push(`*Email flow approved*: ${list(sb.approved).join(', ')}. It runs for contacts who enter from now on.`);
  if (list(sb.rejected).length) lines.push(`*Email flow rejected*: ${list(sb.rejected).join(', ')}. The approved version, if any, keeps running.`);
  if (list(sb.waiting_for_approver_key).length) problems.push(`approved in review but n8n has no APPROVER_KEY: ${list(sb.waiting_for_approver_key).join(', ')}`);
  problems.push(...list(sb.problems));
}
if (!ok(t)) problems.push(`tick failed (${t.statusCode || 'no response'}: ${errText(t)})`);
else {
  const by = Object.entries(tb.by_flow || {}).map(([f, n]) => `${f} ${n}`).join(', ');
  if (tb.sent > 0) lines.push(tb.dry_run
    ? `*Email flows (dry run)*: ${tb.sent} email${tb.sent === 1 ? '' : 's'} written to the outbox, nothing delivered (${by}).`
    : `*Email flows*: ${tb.sent} email${tb.sent === 1 ? '' : 's'} sent (${by}).`);
  if (tb.failed > 0) problems.push(`${tb.failed} send${tb.failed === 1 ? '' : 's'} failed (GET /outbox?status=failed)`);
  for (const n of list(tb.notices)) lines.push(`*Email flows*: ${n}`);
}
if (problems.length) lines.push(`*Email flow problems*: ${problems.slice(0, 5).join('; ')}`);
return [{ json: { notify: lines.length > 0, text: lines.join('\n'), waits: list(sb.created).length > 0,
  sent: ok(t) ? tb.sent || 0 : 0, problems: problems.length } }];
"""


def wf86():
    wf = Workflow(86, "Schedule · Email flows")
    t = schedule(wf, "Every 15 minutes", "*/15 * * * *")
    h = http(wf, "Flow health", "GET",
             "={{ $env.FLOW_URL ? %s + '/health' : $env.CALENDAR_URL + '/health' }}" % FLOWS,
             never_error=True, continue_on_fail=True, full_response=True, timeout=15000)
    rd = code(wf, "Ready?", FLOW_READY)
    go = if_true(wf, "Go?", "={{ $json.go }}")
    opt = dict(key=True, never_error=True, continue_on_fail=True, full_response=True)
    sy = http(wf, "Sync reviews", "POST", "={{ %s }}/reviews/sync" % FLOWS, timeout=120000, approver=True, **opt)
    tk = http(wf, "Tick", "POST", "={{ %s }}/tick" % FLOWS, timeout=300000, **opt)
    bd = code(wf, "Build", FLOW_BUILD)
    gate = code(wf, "Report?", "// Nothing happened, or no destination: no notification.\n"
                               "return " + NOTIFY_ON + " ? $input.all().filter(i => i.json.notify) : [];")
    n = notify(wf, "Notify", "$json.text", waits="$json.waits")
    wf.chain(t, h, rd, go)
    wf.link(go, sy, src_index=0)
    wf.link(go, gate, src_index=1)
    wf.chain(sy, tk, bd, gate, n)
    return wf, {}


EXTRA_FLOWS = [(wf86, PATH_86)]
