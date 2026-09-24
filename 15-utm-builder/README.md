# utm-builder

Deploy **15 of 60** of the local-LLM marketing agent. It builds campaign links with one naming
scheme, so every post, email and ad shows up grouped correctly in analytics.
It uses no LLM.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing`
network. n8n calls it at `http://utm-builder:8000` (env `UTM_URL`).
It is stateless, so it also runs fine on any container host (Render, Fly.io, Railway).

## Run

```bash
docker build -t utm-builder .
docker run --rm -p 8115:8000 utm-builder
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --port 8115
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/conventions` | — | allowed mediums + rules |
| POST | `/build` | `{"url","source","medium","campaign","term"?,"content"?}` | `{"url","params"}` |
| POST | `/parse` | `{"url"}` | `{"base_url","params"}` |

```bash
curl -s localhost:8115/build -H 'content-type: application/json' \
  -d '{"url":"https://example.com/sale","source":"LinkedIn","medium":"social","campaign":"Spring Sale"}'
# {"url":"https://example.com/sale?utm_source=linkedin&utm_medium=social&utm_campaign=spring-sale", ...}
```

`medium` must be one of `/conventions` → `allowed_mediums`, otherwise you get a 422 error.
Existing non-UTM query parameters are kept.

## CI

`.github/workflows/ci.yml` runs the tests, then pushes `ghcr.io/<you>/<repo>:latest` on
every push to `main`.
