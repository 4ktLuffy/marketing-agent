# mcp-connector

Deploy **89 of 90** of the local-LLM marketing agent. It is an **MCP server** that lets Claude use
the business's facts and the task bridge directly: Claude Desktop and Claude Code on your machine,
or claude.ai through **one custom remote connector** (the Free plan allows one). It needs no model
and holds no key that can approve anything.

- **Public facts only.** `get_business_facts` returns the facts from the brand service (05) that
  are **public**, valid on the date and in scope (site, region, channel, segment, plan tier,
  variant): text, value text, required disclosures, scope and valid dates. Everything else is
  listed as `excluded` with its reason, never its value. The connector asks 05 for
  `max_sensitivity=public` and filters again itself.
- **Task packs.** `make_task_pack` creates a task in the task bridge (88) and returns the pack and
  the share preview (what was sent, slotted, withheld).
- **Checked submissions.** `submit_answer` pastes Claude's answer into the task, then submits it:
  every sentence gets an evidence label, each piece becomes a review item. If the split into
  pieces has problems it stops and returns them; `submit_split` sends a split made by hand.
- **Stateless checks.** `check_text` checks any text; nothing is created.
- **State.** `get_task` and `list_blockers`.
- **Internal values stay home.** 88 fills slots with real values, internal ones included,
  because the person approving needs the finished text. Before anything goes back to Claude, the
  connector replaces every internal or restricted fact's value, value text and sentence with its
  slot (`[[partner-rate]]`) and drops quotes from those facts. If it cannot read the fact list to
  know which facts are internal, it returns an error and sends nothing to 88 (fail closed).
- **No approve, no publish.** There is no tool for approving, publishing, exporting, confirming
  or retiring. The HTTP client can only call nine allowlisted paths. The connector refuses to
  start if `APPROVER_KEY`, `FACT_OWNER_KEY` or any `*_APPROVER_KEY` / `*_OWNER_KEY` is set.

## What Claude can and can't do

| Claude can | Claude can't |
|---|---|
| Read public facts valid on a date, for a scope | See internal or restricted values (only `[[slots]]`) |
| Start a task and read its pack | Approve, reject or publish a piece |
| Submit its answer; see blocked pieces, findings, rule problems | Export the ready-to-post text |
| Split an answer by hand and resubmit | Create, change, confirm or retire a fact |
| Check any text without saving it | Touch the content calendar (19) directly |
| Read a task's state and the blockers list | Run reconcile or anything scheduled |

Every submit result says: **a person must approve this in the control room (72); Claude cannot
approve or publish.**

## Tools

| Tool | Calls | Returns |
|---|---|---|
| `get_business_facts(site?, region?, channel?, segment?, plan_tier?, variant?, on_date?)` | 05 `GET /facts/query?max_sensitivity=public` | `facts[{key, slot, text, value_text, required_disclosures, forbidden_phrasing, scope, valid_from, valid_to}]`, `excluded[{key, reason}]`, `fact_set_version` |
| `make_task_pack(goal, pieces[{channel, kind?, max_chars?}], publish_on, scope?, audience?)` | 88 `POST /tasks` | `task_id`, `pack`, `share_preview`, piece keys `p1..pN` |
| `submit_answer(task_id, text, provider="claude")` | 05 `GET /facts/v2` (to hide internal values), 88 `POST /tasks/{id}/paste`, then `POST /tasks/{id}/submit` | per piece: `blocked`, `status`, `findings[{label, sentence, quote, why, blocking, fact_key}]`, `rule_problems`, `calendar_item_id`; or `split_has_problems` + `draft_id` |
| `submit_split(task_id, draft_id, pieces[{piece_key, text}])` | 88 `POST /tasks/{id}/drafts/{d}/split`, then submit | as `submit_answer` |
| `check_text(text, publish_on, scope?, channel?)` | 88 `POST /check` | `blocked`, `findings` |
| `get_task(task_id)` | 88 `GET /tasks/{id}` | pieces, drafts, export readiness, pack |
| `list_blockers()` | 88 `GET /blockers` | `[{kind, count, text, link}]` |

Inputs are validated from JSON Schema (lengths, dates, task id `T-XXXXXX`, at most 10 pieces,
unknown fields refused). Text is capped at `MAX_TEXT_CHARS` (40,000).

## Setup

It needs a running brand service (05) and task bridge (88), and `INTERNAL_API_KEY`.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

### Claude Desktop (local, stdio)

Claude Desktop starts the process itself. In `claude_desktop_config.json` (Settings → Developer →
Edit config):

```json
{
  "mcpServers": {
    "marketing-facts": {
      "command": "/ABSOLUTE/PATH/89-mcp-connector/.venv/bin/python",
      "args": ["-m", "app", "stdio"],
      "env": {
        "PYTHONPATH": "/ABSOLUTE/PATH/89-mcp-connector",
        "INTERNAL_API_KEY": "change-me",
        "BRAND_URL": "http://localhost:8105",
        "TASK_BRIDGE_URL": "http://localhost:8188"
      }
    }
  }
}
```

Restart Claude Desktop; the seven tools appear under the connector. (Anthropic documents local
MCP in Claude Desktop without a plan gate; that the Free plan includes it is not confirmed.)

### Claude Code (local)

Syntax checked against `claude mcp add --help` (not run against this server):

```bash
claude mcp add marketing-facts \
  -e PYTHONPATH="$PWD" -e INTERNAL_API_KEY=change-me \
  -e BRAND_URL=http://localhost:8105 -e TASK_BRIDGE_URL=http://localhost:8188 \
  -- "$PWD/.venv/bin/python" -m app stdio

# or, against the HTTP server:
claude mcp add --transport http marketing-facts https://YOUR-HOST/mcp \
  --header "Authorization: Bearer $MCP_TOKEN"
```

### claude.ai (remote custom connector, Free plan: one)

claude.ai calls the connector **from Anthropic's cloud**, so it needs a **public HTTPS URL**.

1. Make a token: `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.
2. Run the HTTP server on this machine, on localhost only:
   ```bash
   INTERNAL_API_KEY=change-me BRAND_URL=http://localhost:8105 TASK_BRIDGE_URL=http://localhost:8188 \
   MCP_HOST=127.0.0.1 MCP_PORT=8189 MCP_TOKEN=<token> MCP_ALLOWED_HOSTS=<your-subdomain>.ngrok-free.app \
   python -m app http
   ```
3. Put HTTPS in front of it with a tunnel, e.g. `ngrok http 8189` (free: automatic TLS, 20k
   requests a month). Set `MCP_ALLOWED_HOSTS` to the tunnel's hostname: the server checks the
   `Host` header against DNS rebinding and answers 421 to any other host.
4. In claude.ai: Settings → Connectors → Add custom connector → the URL.

**The token.** Claude Code and any client that can send a header use
`Authorization: Bearer <token>`. The claude.ai dialog asks for a URL and optional OAuth client
details; **we have not verified that it can send a static bearer header.** For that case set
`MCP_TOKEN_IN_PATH=true` and add the connector as `https://<host>/mcp/<token>`. This is weaker than
a header (the URL can end up in logs and browser history; the connector's own log shows
`/mcp/***`): treat the URL as the secret, and rotate the token if it leaks. **OAuth 2.1 is the
next step** (the MCP spec's authorization: PKCE, protected-resource metadata, audience check);
until then `/.well-known/*` answers 404 so a client does not start an OAuth flow.

Docker: the image listens on `0.0.0.0`, so it refuses to start without `MCP_TOKEN`:

```bash
docker build -t mcp-connector .
docker run --rm -p 127.0.0.1:8189:8000 --env-file .env mcp-connector
```

## Safety rules the server enforces

| Rule | How |
|---|---|
| No approver/owner key | Startup refuses if `APPROVER_KEY`, `FACT_OWNER_KEY`, `*_APPROVER_KEY` or `*_OWNER_KEY` is non-empty (the name is shown, never the value) |
| Token for non-local HTTP | Startup refuses any `MCP_HOST` other than `127.0.0.1`, `localhost`, `::1` without `MCP_TOKEN` (≥ 24 characters); tokens compared in constant time |
| No approve/publish path | Upstream allowlist of nine method+path pairs, checked before every request; task ids must match `T-[A-Z2-7]{6}` |
| Limits | `TOOL_CALLS_PER_MIN` (30) for tool calls on every transport; `HTTP_REQUESTS_PER_MIN` (120) for HTTP, failed logins included; `MAX_BODY_BYTES` (1 MiB); `MAX_TEXT_CHARS` (40,000) |
| No pasted text in logs | Log lines carry tool, task id, character count and an 8-character hash; the uvicorn access log is off; the SDK logs only at WARNING |

## Verified and not verified

Verified here: the tools and their schemas through the SDK's in-memory client; the real
`python -m app stdio` process listing the tools; the HTTP app (bearer, token in path, 401/421/413/
429, `/health`) through Starlette's test client; a local run of `python -m app http` answering
`initialize` and `tools/call` over curl. 05 and 88 are fakes built from the phase-1 contracts.

Not verified: a real Claude Desktop, Claude Code or claude.ai session against this server; whether
the claude.ai connector dialog can send a bearer header or accepts a token in the URL path; a
tunnel (ngrok, Cloudflare) in front of it; runs against the real 05 and 88 services; the Docker
image (not built).

## Configuration

See `.env.example`. Required: `INTERNAL_API_KEY`, `BRAND_URL`, `TASK_BRIDGE_URL`.

## Known limits

- Hiding internal values replaces a non-public fact's text, value text and value (numbers of two
  digits or more, in plain and thousands-separated forms). A value written a new way (`£1.2k`,
  "seven hundred") in 88's output would not be caught. The pack itself comes from 88, which
  already never includes internal values.
- One rate limit for all callers: the server is meant for one business and one connector.
- Stateless HTTP (no MCP sessions, no server-to-client notifications).
- Not in `01-marketing-stack` compose yet.

## CI

`.github/workflows/ci.yml` runs `pytest` on every push and pull request, and on `main` builds
and pushes the image to GHCR.
