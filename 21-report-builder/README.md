# report-builder

Deploy **21 of 60** of the local-LLM marketing agent. It turns the KPIs from
`20-analytics-ingest` (plus optional LLM-written highlights) into a one-page HTML report
and a markdown version of the same report. The HTML has KPI tiles with change arrows, a
bar chart of sessions by channel, and a table by channel. The weekly report workflow
(deploy 41) emails or posts it. It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://report-builder:8000` (env `REPORT_URL`).
It is stateless, so it also runs fine on any container host (Render, Fly.io, Railway).

## Run

```bash
docker build -t report-builder .
docker run --rm -p 8121:8000 report-builder
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8121
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/render` | `{"title","period","kpis","highlights_markdown"?,"currency"?}` | `{"html","markdown"}` |

`kpis` is the exact response of `GET /kpis` on `20-analytics-ingest`. `period` is a
display string (`"14–20 Sep 2026"`) or a `{"from","to"}` object.

```bash
KPIS=$(curl -s 'localhost:8120/kpis?from=2026-09-14&to=2026-09-20')
curl -s localhost:8121/render -H 'content-type: application/json' \
  -d "{\"title\":\"Weekly marketing report\",\"period\":\"14–20 Sep 2026\",\"kpis\":$KPIS,\"highlights_markdown\":\"- Paid social CPA fell\",\"currency\":\"\$\"}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["html"])' > report.html
```

- The HTML is one self-contained page: CSS inline in a `<style>` tag, an inline SVG
  chart, and no scripts, fonts or images to load. It follows the reader's light or dark
  mode.
- Tiles show `▲`/`▼` with the `delta_pct` value. Green means better, red means worse.
  For **cost per conversion (cpa)**, lower is better, so `▼` is green. **Spend** is
  neutral (grey), because higher spend is neither good nor bad by itself. Without
  `delta_pct`, a tile shows "no comparison".
- The bar chart shows sessions by channel. If no channel has sessions, it shows clicks.
- The title, period, channel names and highlights are escaped. In the highlights,
  markdown is rendered, raw HTML is escaped, and non-http links become `#`.
- `currency` (optional, default empty) is a prefix for money values, such as `"$"`.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
