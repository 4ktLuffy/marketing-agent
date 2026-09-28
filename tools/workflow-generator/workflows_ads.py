"""Paid ads (84-ads-sync): the daily sync + alerts workflow, shipped as 84-ads-sync/n8n/workflow.json,
and the code of 41's "Ads" section.

84 reads spend, conversions and revenue from the Meta Marketing API and the Google Ads API
(read-only; nothing can change a budget or an ad) and computes CPL, ROAS, pacing and alerts in
code. This workflow only triggers the sync, asks for the alerts that are new, and sends them.
Contract: 84-ads-sync/README.md.
"""
from n8nlib import NOTIFY_ON, Workflow, code, http, if_true, notify, schedule

# The workflow JSON ships inside the service's repo, not as an NN-wf-* deploy.
PATH_84 = "84-ads-sync/n8n/workflow.json"
ADS = "$env.ADS_URL.replace(/\\/+$/, '')"

HELPERS = r"""
const ok = r => r && r.statusCode >= 200 && r.statusCode < 300;
const bodyOf = r => (r && r.body && typeof r.body === 'object' ? r.body : {});
const errText = r => { r = r || {}; const b = r.body; const e = (b && typeof b === 'object' ? (b.detail ?? b.message) : b) ?? r.error?.message ?? r.error; return e == null || e === '' ? 'no answer' : String(typeof e === 'object' ? JSON.stringify(e) : e).slice(0, 300); };
"""

ADS_READY = HELPERS + r"""
const r = $input.first().json;
const b = bodyOf(r);
if (!$env.ADS_URL) return [{ json: { go: false, notify: false, text: '*Paid ads*: ADS_URL is not set. Nothing to do.' } }];
if (!ok(r)) return [{ json: { go: false, notify: true, text: `*Paid ads*: ads-sync (84) did not answer (${r.statusCode || 'no response'}: ${errText(r)}).` } }];
if (!b.configured) return [{ json: { go: false, notify: false,
  text: `*Paid ads*: no ad platform is configured (${(b.config_errors || []).join('; ')}). See 84-ads-sync/README.md.` } }];
return [{ json: { go: true } }];
"""

# Pure: tests/ads_test.js runs it. Sync problems always notify; alerts only when new.
ADS_BUILD = HELPERS + r"""
const s = $('Sync').first().json, a = $('Check alerts').first().json;
const sb = bodyOf(s), ab = bodyOf(a);
const lines = [];
const problems = [];
if (!ok(s)) problems.push(`sync failed (${s.statusCode || 'no response'}: ${errText(s)})`);
for (const [p, r] of Object.entries(sb.platforms || {})) if (r && r.ok === false) problems.push(`${p}: ${r.error}`);
for (const w of sb.warnings || []) problems.push(w);
const fresh = ok(a) && Array.isArray(ab.new) ? ab.new : [];
if (fresh.length) {
  lines.push(`*Paid ads alerts* (as of ${ab.as_of}):`);
  for (const x of fresh) lines.push(`- [${x.severity}] ${x.rule.replace(/_/g, ' ')}: ${x.message}`);
}
if (!ok(a)) problems.push(`alerts not checked (${a.statusCode || 'no response'}: ${errText(a)})`);
if (problems.length) lines.push(`${lines.length ? '\n' : ''}*Paid ads sync*: ${problems.slice(0, 5).join('; ')}`);
return [{ json: { notify: lines.length > 0, text: lines.join('\n'), new_alerts: fresh.length, problems: problems.length } }];
"""

# 41 weekly report: the Ads section, from 84 GET /summary (every number computed by 84).
WEEKLY_ADS = HELPERS + r"""
const prev = $('Visibility section').first().json;
const r = $('Ads summary').first().json || {};
const b = $env.ADS_URL && ok(r) ? bodyOf(r) : null;
if (!b || !b.has_data || !Array.isArray(b.facts)) return [{ json: prev }];
const w = b.window.current;
const facts = b.facts.filter(f => !f.startsWith('alert '));
const lines = ['### Ads', `Paid ads ${w.start} to ${w.end} vs the same number of days before. Every number is computed by ads-sync (84) from the platforms' reports, not by the model.`, '',
  ...facts.slice(0, 14).map(f => `- ${f}`)];
const un = Object.entries(b.unmapped_spend || {}).filter(([, v]) => v > 0);
if (un.length) lines.push('', `Spend not mapped to a campaign: ${un.map(([c, v]) => `${v.toFixed(2)} ${c}`).join(', ')} (84 \`POST /mappings\`, or put the campaign slug in the ad campaign's name or utm_campaign).`);
if ((b.alerts || []).length) {
  lines.push('', 'Open alerts:', '', ...b.alerts.slice(0, 6).map(x => `- [${x.severity}] ${x.message}`));
}
return [{ json: { ...prev, markdown: [prev.markdown, lines.join('\n')].filter(Boolean).join('\n\n') } }];
"""


def wf84():
    wf = Workflow(84, "Schedule · Paid ads sync and alerts")
    t = schedule(wf, "Daily 07:30", "30 7 * * *")
    h = http(wf, "Ads health", "GET",
             "={{ $env.ADS_URL ? %s + '/health' : $env.CALENDAR_URL + '/health' }}" % ADS,
             never_error=True, continue_on_fail=True, full_response=True, timeout=15000)
    rd = code(wf, "Ready?", ADS_READY)
    go = if_true(wf, "Go?", "={{ $json.go }}")
    opt = dict(key=True, never_error=True, continue_on_fail=True, full_response=True)
    # 7 days: the platforms restate recent days as conversions are attributed.
    sy = http(wf, "Sync", "POST", "={{ %s }}/sync" % ADS, "={{ JSON.stringify({ days: 7 }) }}", timeout=300000, **opt)
    ck = http(wf, "Check alerts", "POST", "={{ %s }}/alerts/check" % ADS, timeout=60000, **opt)
    bd = code(wf, "Build", ADS_BUILD)
    gate = code(wf, "Report?", "// Nothing new, or no destination: no notification.\n"
                               "return " + NOTIFY_ON + " ? $input.all().filter(i => i.json.notify) : [];")
    n = notify(wf, "Notify", "$json.text")
    wf.chain(t, h, rd, go)
    wf.link(go, sy, src_index=0)
    wf.link(go, gate, src_index=1)
    wf.chain(sy, ck, bd, gate, n)
    return wf, {}


def weekly_ads_nodes(wf):
    """(Ads summary node, Ads section node) for 41. The summary goes before 'Action data' so its
    facts reach the weekly_actions prompt; the section goes after the visibility line."""
    s = http(wf, "Ads summary", "GET",
             "={{ $env.ADS_URL ? %s + '/summary' : $env.CAMPAIGNS_URL + '/health' }}" % ADS,
             query={"days": "7", "end": "={{ $('Last week').first().json.to }}"},
             full_response=True, never_error=True, continue_on_fail=True, timeout=60000)
    return s


EXTRA_ADS = [(wf84, PATH_84)]
