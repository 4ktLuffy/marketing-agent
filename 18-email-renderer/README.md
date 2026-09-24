# email-renderer

Deploy **18 of 60** of the local-LLM marketing agent. It turns an email the LLM wrote in
markdown into HTML that survives Gmail and Outlook (table layout, 600 px wide, inline
styles only, no external CSS, no JavaScript) plus a matching plain-text part.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://email-renderer:8000` (env `EMAIL_RENDER_URL`).
It is stateless, so it also runs fine on any container host (Render, Fly.io, Railway).

## Run

```bash
docker build -t email-renderer .
docker run --rm -p 8118:8000 email-renderer
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8118
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/render` | `{"subject","preheader"?,"body_markdown","cta_text"?,"cta_url"?,"footer"?}` | `{"html","text"}` |

```bash
curl -s localhost:8118/render -H 'content-type: application/json' \
  -d '{"subject":"Spring sale","preheader":"20% off this week","body_markdown":"# Hi\n\nOur **sale** starts today.","cta_text":"Shop now","cta_url":"https://example.com/sale","footer":"Acme Inc, 1 Main St"}'
# {"html":"<!DOCTYPE html>...","text":"Hi\n\nOur sale starts today.\n\nShop now: https://example.com/sale\n\n--\nAcme Inc, 1 Main St\nUnsubscribe: {{unsubscribe_url}}\n"}
```

- `preheader` goes into a hidden span, so inbox previews show it next to the subject.
- The CTA button is drawn only when both `cta_text` and `cta_url` are set. It is a
  "bulletproof" button (VML for Outlook desktop, a styled link elsewhere).
  `cta_url` must be an absolute `http(s)` URL, otherwise you get a 422 error.
- The footer always ends with an Unsubscribe link to the literal `{{unsubscribe_url}}`.
  Your ESP (Mailchimp, Brevo, Listmonk, ...) replaces it; change the placeholder there if
  it uses another syntax.
- `subject`, `preheader` and `footer` are HTML-escaped. Raw HTML inside `body_markdown` is
  escaped too, and links that are not `http(s)`, `mailto:` or `{{...}}` become `#`.
- In `text`, markdown is stripped: links become `text (url)`, bullets become `- `, and the
  CTA becomes `cta_text: cta_url`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
