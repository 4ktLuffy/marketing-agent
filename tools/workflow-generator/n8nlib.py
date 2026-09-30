"""Helpers that emit n8n 2.x workflow JSON with node versions verified against n8n 2.40.5."""
import json
import uuid

ERROR_WF = "mktWf43ErrorHand"
OLLAMA_CRED = {"ollamaApi": {"id": "mktOllamaCred001", "name": "Ollama (local)"}}
# Forms (approval, knowledge, rules) require a login: without one, anyone who can reach the
# n8n URL could approve and publish (security audit, finding C1).
# Optional hosted chat model (Groq or any OpenAI-compatible API) for the chat agent's
# "hosted" variant; import-n8n.sh creates it from CHAT_API_KEY / CHAT_BASE_URL.
HOSTED_CRED = {"openAiApi": {"id": "mktHostedChat001", "name": "Hosted chat model"}}
# The chat agent's model through the LLM gateway (03 POST /v1/chat/completions), so its calls
# show on the control room's Activity page. import-n8n.sh creates it: base URL
# http://llm-gateway:8000/v1, API key = INTERNAL_API_KEY, custom header X-Caller: 24 Chat agent.
GATEWAY_CHAT_CRED = {"openAiApi": {"id": "mktGatewayChat01", "name": "LLM gateway (chat)"}}
FORMS_CRED = {"httpBasicAuth": {"id": "mktFormsLogin001", "name": "Forms login"}}

WF_IDS = {
    24: "mktWf24ChatAgent", 25: "mktWf25BlogWrite", 26: "mktWf26SocialWri",
    27: "mktWf27AdCopy000", 28: "mktWf28EmailWrit", 29: "mktWf29SeoBrief0",
    30: "mktWf30Repurpose", 31: "mktWf31ResearchU", 32: "mktWf32KeywordRe",
    33: "mktWf33Calendar0", 34: "mktWf34KbAnswer0", 35: "mktWf35QualityGa",
    36: "mktWf36TrendDige", 37: "mktWf37Competito", 38: "mktWf38Approval0",
    39: "mktWf39Publisher", 40: "mktWf40ContentPl", 41: "mktWf41WeeklyRep",
    42: "mktWf42KbIngest0", 43: "mktWf43ErrorHand",
    47: "mktWf47PlanCampa", 48: "mktWf48CampDraft", 49: "mktWf49ReviseDra",
    50: "mktWf50LearnRevi", 51: "mktWf51RulesForm", 52: "mktWf52CampMeasu",
    53: "mktWf53Campaigns", 56: "mktWf56AnalySync",
    57: "mktWf57ContentFm", 59: "mktWf59Recycler0", 60: "mktWf60RevReply0",
    64: "mktWf64ContEngin", 65: "mktWf65EngDraft0",
    66: "mktWf66Newslettr",
    68: "mktWf68ContRefre", 69: "mktWf69SeoOpport",
    72: "mktWf72ApplyDeci",  # 72-control-room/n8n/workflow.json (decision webhook)
    74: "mktWf74ExpManagr", 75: "mktWf75ExpAnalys", 76: "mktWf76ExpForm00",
    77: "mktWf77ClipsTool",
    81: "mktWf81TrackComp",  # 78 is a service (ad-library-sync); 79, 80 are services too
    83: "mktWf83AiVisibil",  # 82 is a service (ai-visibility)
    84: "mktWf84AdsSync00",  # 84-ads-sync/n8n/workflow.json (daily sync + alerts)
    85: "mktWf85ClientRep",
    86: "mktWf86FlowRunnr",  # 86-flow-runner/n8n/workflow.json (review sync + tick every 15 min)
}
assert all(len(v) == 16 for v in WF_IDS.values())

LLM_TIMEOUT = 300000  # ms; a 7B model on a laptop can take minutes for long copy


def _uid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


class Workflow:
    def __init__(self, num: int, name: str, error_workflow: bool = True):
        self.num, self.name = num, name
        self.id = WF_IDS[num]
        self.nodes: list[dict] = []
        self.connections: dict = {}
        self.error_workflow = error_workflow
        self._x = 0

    def add(self, name, type_, version, params, pos=None, **extra):
        if pos is None:
            pos = [self._x, 300]
            self._x += 240
        node = {
            "id": _uid(f"{self.id}/{name}"),
            "name": name,
            "type": type_,
            "typeVersion": version,
            "position": pos,
            "parameters": params,
        }
        node.update(extra)
        self.nodes.append(node)
        return name

    def link(self, src, dst, kind="main", src_index=0, dst_index=0):
        outs = self.connections.setdefault(src, {}).setdefault(kind, [])
        while len(outs) <= src_index:
            outs.append([])
        outs[src_index].append({"node": dst, "type": kind, "index": dst_index})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def to_json(self) -> str:
        settings = {"executionOrder": "v1"}
        if self.error_workflow:
            settings["errorWorkflow"] = ERROR_WF
        wf = {
            "id": self.id,
            "name": f"{self.num} · {self.name}",
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": settings,
            "active": False,
            "pinData": {},
            "tags": [],
        }
        return json.dumps(wf, indent=2, ensure_ascii=False) + "\n"


# ---------- node builders ----------

def sub_trigger(wf, inputs: list[tuple[str, str]], name="Start"):
    """Execute Workflow Trigger with typed inputs [(name, type)]."""
    return wf.add(name, "n8n-nodes-base.executeWorkflowTrigger", 1.1, {
        "inputSource": "workflowInputs",
        "workflowInputs": {"values": [{"name": n, "type": t} for n, t in inputs]},
    })


# Approving and marking published need a second key that only n8n holds (19 checks it), so a
# service that has INTERNAL_API_KEY (e.g. a compromised scraper) still can't approve copy.
APPROVER_HEADER = {"name": "X-Approver-Key", "value": "={{ $env.APPROVER_KEY }}"}


def caller_header(wf) -> dict:
    """Every gateway call says who is asking ("26 Social writer"); the gateway's activity log
    (03 GET /v1/activity) and the control room's Activity page show it. A literal, not an expression."""
    kind, sep, rest = wf.name.partition(" · ")
    label = rest if sep and kind in ("Tool", "Schedule", "Background", "Form") else wf.name
    return {"name": "X-Caller", "value": f"{wf.num} {label}"[:80]}


def http(wf, name, method, url, body=None, *, key=False, query=None, llm=False,
         never_error=False, continue_on_fail=False, pos=None, timeout=None, full_response=False,
         approver=False):
    params = {"method": method, "url": url, "options": {}}
    headers = ([{"name": "X-API-Key", "value": "={{ $env.INTERNAL_API_KEY }}"}] if key else []) \
        + ([APPROVER_HEADER] if key and approver else []) \
        + ([caller_header(wf)] if llm else [])
    if headers:
        params["sendHeaders"] = True
        params["headerParameters"] = {"parameters": headers}
    if query:
        params["sendQuery"] = True
        params["queryParameters"] = {"parameters": [{"name": k, "value": v} for k, v in query.items()]}
    if body is not None:
        params["sendBody"] = True
        params["specifyBody"] = "json"
        params["jsonBody"] = body
    if llm or timeout:
        params["options"]["timeout"] = timeout or LLM_TIMEOUT
    if never_error or full_response:
        # fullResponse keeps a JSON list as ONE item ({body: [...]}); otherwise n8n splits it
        # into one item per element, which multiplies every node after it.
        params["options"]["response"] = {"response": {k: v for k, v in
                                                      (("neverError", never_error), ("fullResponse", full_response)) if v}}
    extra = {"onError": "continueRegularOutput"} if continue_on_fail else {}
    return wf.add(name, "n8n-nodes-base.httpRequest", 4.2, params, pos=pos, **extra)


def gateway(wf, name, prompt, vars_js, pos=None, **kw):
    """POST the llm-gateway. vars_js is a JS object literal (without braces' JSON.stringify).
    Extra keyword arguments (e.g. continue_on_fail) go to http()."""
    body = "={{ JSON.stringify({ prompt: '%s', vars: %s }) }}" % (prompt, vars_js)
    return http(wf, name, "POST", "={{ $env.GATEWAY_URL }}/v1/run", body, key=True, llm=True, pos=pos, **kw)


def code(wf, name, js, *, each=False, pos=None):
    params = {"jsCode": js.strip() + "\n"}
    if each:
        params["mode"] = "runOnceForEachItem"
    return wf.add(name, "n8n-nodes-base.code", 2, params, pos=pos)


def if_true(wf, name, bool_expr, pos=None):
    return wf.add(name, "n8n-nodes-base.if", 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [{
                "id": _uid(f"{wf.id}/{name}/cond"),
                "leftValue": bool_expr,
                "rightValue": "",
                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
            }],
            "combinator": "and",
        },
        "options": {},
    }, pos=pos)


def loop_one_by_one(wf, name, pos=None):
    """Loop Over Items (Split in Batches v3), one item per round. Output 0 = done (every item
    fed back into it), output 1 = the current item. Used where each item makes a slow local
    LLM call: parallel requests queue in Ollama and the last ones hit the timeout."""
    return wf.add(name, "n8n-nodes-base.splitInBatches", 3, {"batchSize": 1, "options": {}}, pos=pos)


def merge_append(wf, name, pos=None):
    return wf.add(name, "n8n-nodes-base.merge", 3, {}, pos=pos)


def schedule(wf, name, cron):
    return wf.add(name, "n8n-nodes-base.scheduleTrigger", 1.2, {
        "rule": {"interval": [{"field": "cronExpression", "expression": cron}]},
    })


def mapper(fields: dict[str, str], types: dict[str, str] | None = None) -> dict:
    """resourceMapper value for Execute Workflow / Tool Workflow inputs."""
    types = types or {}
    return {
        "mappingMode": "defineBelow",
        "value": fields,
        "matchingColumns": [],
        "schema": [{
            "id": k, "displayName": k, "required": False, "defaultMatch": False,
            "display": True, "canBeUsedToMatch": True, "type": types.get(k, "string"), "removed": False,
        } for k in fields],
        "attemptToConvertTypes": False,
        "convertFieldsToString": False,
    }


def call_workflow(wf, name, num, fields, types=None, pos=None, wait=True, mode="once"):
    """mode "once": one sub-run gets all items; "each": one sub-run per item."""
    return wf.add(name, "n8n-nodes-base.executeWorkflow", 1.2, {
        "workflowId": {"__rl": True, "mode": "id", "value": WF_IDS[num]},
        "workflowInputs": mapper(fields, types),
        "mode": mode,
        "options": {"waitForSubWorkflow": wait},
    }, pos=pos)


def dynamic_http(wf, name, pos=None, timeout=None, approver=False):
    """HTTP node whose method/url/body come from the item: {method, url, body (JSON string)}.
    Used to run a list of API operations built in a Code node."""
    params = {
        "method": "={{ $json.method }}",
        "url": "={{ $json.url }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "X-API-Key", "value": "={{ $env.INTERNAL_API_KEY }}"}]
                             + ([APPROVER_HEADER] if approver else [])},
        "sendBody": True,  # a boolean expression here is ignored by n8n; GET bodies are ignored by our services
        "specifyBody": "json",
        "jsonBody": "={{ $json.body || '{}' }}",
        # Requests for different items run IN PARALLEL (batching only delays them, it does not
        # wait for each to finish). Operations on the same record must go in separate
        # stages: see staged_ops().
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}}},
    }
    if timeout:
        params["options"]["timeout"] = timeout
    return wf.add(name, "n8n-nodes-base.httpRequest", 4.2, params, pos=pos)


# A Code-node helper: turn a list of operations into items, or one harmless no-op so a
# linear chain keeps running when the list is empty.
OPS_TO_ITEMS = r"""
function opsToItems(ops, base) {
  if (!ops.length) return [{ json: { method: 'GET', url: `${base}/health`, body: '{}', noop: true } }];
  return ops.map(o => ({ json: { ...o, body: JSON.stringify(o.body || {}) } }));
}
"""


# Where notifications go, from NOTIFY_FORMAT: generic (default; {text, content} to
# NOTIFY_WEBHOOK_URL, unchanged), slack ({text}, <url|label> links), discord ({content},
# <= 2000 chars, no @mentions) or telegram (Bot API sendMessage, HTML, <= 4096 chars; needs
# TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, not NOTIFY_WEBHOOK_URL). Pure function, so
# tests/notify_format_test.js runs it in node. Secrets never enter its output: the url holds
# the placeholders <TELEGRAM_BOT_TOKEN> / <NOTIFY_WEBHOOK_URL>, which only the HTTP node fills
# in, so execution data shows where a message went without the token or webhook URL.
# `waits`: something waits for a person, so the message ends with the approval form link.
NOTIFY_FORMAT_JS = r"""
function notifyRequest(text, env, waits) {
  const fmt = String(env.NOTIFY_FORMAT || 'generic').trim().toLowerCase();
  const telegram = fmt === 'telegram';
  if (telegram ? !(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) : !env.NOTIFY_WEBHOOK_URL) return null;
  const plain = String(text ?? '');
  const form = `${String(env.N8N_PUBLIC_URL || 'http://localhost:5678/').trim().replace(/\/?$/, '/')}form/mkt-content-approval`;
  const link = waits && !plain.includes(form) ? form : null;   // already in the text: once is enough
  // Cut the text (never the link) to the longest prefix whose rendered message fits:
  // escaping can make the rendered text several times longer, so search for it.
  const fit = (render, max) => {
    if (render(plain).length <= max) return render(plain);
    let lo = 0, hi = plain.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (render(plain.slice(0, mid).trimEnd() + '…').length <= max) lo = mid; else hi = mid - 1;
    }
    return render(plain.slice(0, lo).trimEnd() + '…');
  };
  const mdLink = /\[([^\]\n]+)\]\((https?:\/\/[^\s)"<>]+)\)/g;
  const oneStar = /(^|[^*\w])\*(?![\s*])([^*\n]*?[^\s*])\*(?![*\w])/g;   // Slack-style *bold*
  const heading = /^#{1,6}[ \t]+(.+)$/gm;
  if (telegram) {
    const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const render = t => esc(t)
      .replace(mdLink, '<a href="$2">$1</a>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(oneStar, '$1<b>$2</b>').replace(heading, '<b>$1</b>')
      + (link ? `\n\n<a href="${esc(link)}">Open the approval form</a>` : '');
    const api = String(env.TELEGRAM_API_URL || 'https://api.telegram.org').trim().replace(/\/+$/, '');
    return { format: 'telegram', url: `${api}/bot<TELEGRAM_BOT_TOKEN>/sendMessage`, body: {
      chat_id: String(env.TELEGRAM_CHAT_ID).trim(), text: fit(render, 4096), parse_mode: 'HTML',
      disable_web_page_preview: true } };
  }
  const url = '<NOTIFY_WEBHOOK_URL>';
  if (fmt === 'slack') {
    const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const render = t => esc(t)
      .replace(mdLink, '<$2|$1>').replace(/\*\*([^*\n]+)\*\*/g, '*$1*').replace(heading, '*$1*')
      + (link ? `\n\n<${link}|Open the approval form>` : '');
    return { format: 'slack', url, body: { text: fit(render, 40000) } };
  }
  if (fmt === 'discord') {
    // <url> stops link previews; Discord bold is **x**; nothing in the text can ping anyone.
    const render = t => t.replace(oneStar, '$1**$2**').replace(/(^|\s)(https?:\/\/[^\s<>]+)/g, '$1<$2>')
      + (link ? `\n\nApproval form: <${link}>` : '');
    return { format: 'discord', url, body: { content: fit(render, 2000), allowed_mentions: { parse: [] } } };
  }
  const t = link ? `${plain.trimEnd()}\n\nApproval form: ${link}` : plain;
  return { format: 'generic', url, body: { text: t, content: t } };   // the payload before NOTIFY_FORMAT
}
"""

# JS condition: is a notification destination configured? For the "Webhook set?" gates.
NOTIFY_ON = ("(String($env.NOTIFY_FORMAT || '').trim().toLowerCase() === 'telegram'"
             " ? $env.TELEGRAM_BOT_TOKEN && $env.TELEGRAM_CHAT_ID : $env.NOTIFY_WEBHOOK_URL)")
GATE_NOTIFY = "// Skip notifying when no destination is configured.\nreturn %s ? $input.all() : [];" % NOTIFY_ON


def notify(wf, name, text_expr, pos=None, waits=None):
    """Send text_expr (JS; $json = the item) per NOTIFY_FORMAT: a Code node that formats
    (NOTIFY_FORMAT_JS) and one HTTP node. `waits` (JS on the item, e.g. "item.json.saved > 0"):
    the summary means something waits for a person, so it ends with the approval form link.
    Returns the Code node, the entry point."""
    text_js = text_expr.replace("$json", "item.json")
    c = code(wf, f"{name} format", NOTIFY_FORMAT_JS + """
return $input.all().flatMap(item => {
  const req = notifyRequest(%s, $env, %s);
  return req ? [{ json: req }] : [];
});
""" % (text_js, (waits or "false").replace("$json", "item.json")), pos=pos)
    h = http(wf, name, "POST",
             # function replacements: a "$" in a token or URL is not a replace pattern
             "={{ $json.url.replace('<TELEGRAM_BOT_TOKEN>', () => $env.TELEGRAM_BOT_TOKEN || '')"
             ".replace('<NOTIFY_WEBHOOK_URL>', () => $env.NOTIFY_WEBHOOK_URL || '') }}",
             "={{ JSON.stringify($json.body) }}", continue_on_fail=True,
             pos=[pos[0] + 240, pos[1]] if pos else None)
    wf.link(c, h)
    return c


def staged_ops(wf, ops_node: str, stages: int, base_expr: str, prefix: str = "", approver: bool = False) -> list[str]:
    """Run operations from `ops_node` (items {stage, method, url, body}) one stage at a time.

    n8n finishes a node before the next starts, so stage 2 (e.g. "move to review") always
    sees stage 1 (e.g. "set to draft") done. Returns [code1, run1, code2, run2, ...] names;
    the caller chains them. Run node k's items pair index-by-index with Code node k's items.
    """
    names = []
    for k in range(1, stages + 1):
        c = code(wf, f"{prefix}Stage {k}", OPS_TO_ITEMS + f"""
const ops = $('{ops_node}').all().map(i => i.json).filter(o => o.stage === {k});
return opsToItems(ops, {base_expr});
""")
        r = dynamic_http(wf, f"{prefix}Run stage {k}", approver=approver)
        names += [c, r]
    return names


# Ops nodes return raw operations; an empty list still yields one item so the chain runs.
RAW_OPS = r"""
function rawOps(ops) { return ops.length ? ops.map(o => ({ json: o })) : [{ json: { stage: 0 } }]; }
"""
