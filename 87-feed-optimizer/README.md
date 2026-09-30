# feed-optimizer

Deploy **87 of 90** of the local-LLM marketing agent. It writes **better product titles** (and,
if you want, descriptions) for a small shop's **Google Merchant Center product feed**.

- **Upload the feed you already have.** A CSV or TSV file with a header row, the format Merchant
  Center reads (`id, title, description, brand, gtin, mpn, color, size, material, ...`). Columns
  it does not know are kept as they are.
- **Every product gets a title.** The model (through the llm-gateway, 03) proposes one per
  product: brand first, then what the product is, then the colour, size and material the row
  has. A **rule-based title** (no LLM) is computed for every product too.
- **Checked in code, not trusted.** A proposed title is rejected when it has a number, a colour,
  material, size, gender or age word, a claim ("organic", "waterproof") or a brand that the
  product's own row does not have, promotional text ("free shipping", "best", "sale"), ALL CAPS,
  or more than 150 characters. The rule-based title is used instead. Every rejection is counted
  with its reason.
- **A person approves.** Nothing changes until someone approves products with the approver key.
- **Nothing is uploaded anywhere.** The export is your file with only the approved `title` and
  `description` cells replaced: same columns, same order, same rows. `id`, `gtin`, `mpn`,
  `price`, `sale_price`, `link`, `image_link`, `availability` and `condition` are never changed;
  the export writes them from the uploaded bytes and reads the result back to check it. You
  upload the file to Merchant Center yourself, or use the **supplemental feed** (`id,title`) to
  test the new titles without touching your main feed.

Why titles: in randomised field experiments on a large marketplace, generative-AI listing work
moved sales by 0 % to 16.3 %, most for small sellers ([arXiv 2510.12049](https://arxiv.org/abs/2510.12049)).
Those are platform-scale results. A small shop cannot confirm a lift quickly; see *Measuring*.

## Where to deploy

**Docker host** that runs `01-marketing-stack`, as a container on the `marketing` network
(compose service `feed-optimizer`, **full** profile, port `127.0.0.1:8187`), with a volume on
`/data`. The control room (72) lists the uploads and serves the exports (env `FEED_URL`). It is
internal only: a client install closes its port (`docker-compose.private.yml`). It keeps state in
SQLite, so run exactly one instance and back up the volume. It calls 03 on the same network.

## Run

```bash
docker build -t feed-optimizer .
docker run --rm -p 8187:8000 --env-file .env -v feed-data:/data feed-optimizer
```

Local without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
INTERNAL_API_KEY=change-me APPROVER_KEY=change-me-too DB_PATH=./feeds.sqlite uvicorn app.main:app --port 8187
```

## How to use it

```bash
K='X-API-Key: change-me'
# 1. Upload the feed (the body is the file). Returns the batch id and counts.
curl -s 'localhost:8187/batches?name=products.tsv' -H "$K" --data-binary @products.tsv
# 2. Proposals, 20 products per call (a local model takes a few seconds per product).
#    Call again until "pending" is 0. "descriptions": true also rewrites descriptions.
curl -s localhost:8187/batches/1/propose -H "$K" -H 'content-type: application/json' -d '{"limit": 20}'
# 3. Read before/after, with the reasons a model proposal was rejected.
curl -s 'localhost:8187/batches/1?only=changed' -H "$K"
# 4. Approve all changed products, or some: {"ids": ["A001", "A003"]}.
curl -s localhost:8187/batches/1/approve -H "$K" -H 'X-Approver-Key: change-me-too' \
     -H 'content-type: application/json' -d '{"all": true, "approved_by": "Sam"}'
# 5. Download. The full feed, or the supplemental feed with only id,title(,description).
curl -s localhost:8187/batches/1/export.tsv -H "$K" -o products-optimized.tsv
curl -s localhost:8187/batches/1/supplemental.tsv -H "$K" -o products-supplemental.tsv
```

On a client install (port closed) run the same calls from inside the stack, or open
**Control room → More → Product feed** for the list and the downloads.

**Supplemental feed.** In Merchant Center, add it as a supplemental data source for your main
feed. It overrides only the columns it has, matched by `id`, so you can try the new titles on
part of your catalogue and remove it to go back. When an approved product has a new
description, the file also has a `description` column; approved products whose description did
not change carry the description as it was at upload. Re-export after your main feed changes.

## Endpoints

Every endpoint except `/health` needs the header `X-API-Key: $INTERNAL_API_KEY` (a catalogue
is business data). 🔐 marks the ones that also need `X-Approver-Key: $APPROVER_KEY`. With
`APPROVER_KEY` unset they are refused (`503`).

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok","gateway","max_rows","max_bytes"}` |
| POST | `/batches` | the file as the body; `?name=products.tsv` | the batch summary (`201`), with `unknown_columns`; `422` with the reason for a file it cannot read, `413` when too large |
| GET | `/batches` | `?limit=50` | every batch, newest first, with counts |
| GET | `/batches/{id}` | `?only=all\|changed\|rejected\|approved\|pending&limit=&offset=` | the summary and `items`: per product `title` and `description` (`before`, `after`, `source`, `model`, `rejected` reasons), `rule_based`, `warnings`, `approved` |
| POST | `/batches/{id}/propose` | `{"limit": 20, "descriptions": false, "llm": true}` | proposals for the next pending products; `409` while another run is going |
| POST | `/batches/{id}/approve` 🔐 | `{"ids": [...]}` or `{"all": true}`, `"approved_by"?` | `{"approved": [...], "skipped": [...]}` |
| POST | `/batches/{id}/revoke` 🔐 | `{"ids": [...]}` or `{"all": true}` | `{"revoked": n}` |
| GET | `/batches/{id}/export.csv` / `.tsv` | — | the full feed with the approved changes |
| GET | `/batches/{id}/supplemental.csv` / `.tsv` | — | `id,title(,description)` of the approved products |
| DELETE | `/batches/{id}` | — | `{"deleted": id}` |

The summary counts: `products`, `pending` (no proposal yet), `proposed` (a change is proposed),
`unchanged`, `titles` by source (`model`, `rule_based`, `original`), `descriptions_from_model`,
`rejected` (`titles`, `descriptions`, and products per reason), `errors` (gateway errors for one
product), `approved`.

**Title sources.** `model`: the model's title passed every check. `rule_based`: the model's title
was rejected, failed, or no model is configured, and the rule-based title passed. `original`:
nothing better passed (or the model returned the same title). An approved product keeps what
was approved even if proposals run again.

**Export in the other format.** `export.csv` of a TSV upload (or the other way round) has the same
values, written by Python's `csv` module; only the upload's own format is byte for byte.

## The rule-based title

Brand + gender or age (`Women's`, `Men's`, `Kids'`) + the original title, cleaned (promotional
phrases, prices, `!` and symbols removed, ALL CAPS words in normal case) + the product type
(only when the title has three words or fewer and lacks it) + ` - ` + the colour, size, material
and pattern the title does not mention yet. When the original title contradicts a filled field
(it says Blue, `color` says White), the word is left out and the product gets a warning. It uses
only words from the row and must pass the same checks as the model's title.

## The checks

What Google asks of a title (Merchant Center Help, "Title [title]",
[support.google.com/merchants/answer/6324415](https://support.google.com/merchants/answer/6324415),
from memory, not re-read today): at most 150 characters; the important details first, because
shoppers often see only the first 70 characters or fewer; for apparel brand, gender, product
type and attributes such as colour, size and material; no promotional text such as "free
shipping", no ALL CAPS and no gimmicky characters; the title must match the product on the
landing page. Descriptions: at most 5000 characters, plain text
([answer/6324468](https://support.google.com/merchants/answer/6324468)).

The checks here (`app/checks.py`), for the model's title and description:

| Reason | Rejected when |
|---|---|
| `number` | a number (or `two`, `pair`, `dozen`) is in none of the row's fields, or a pack count ("3-Pack", "pack of 3") has no matching count in the row. Price, sale price, id, gtin and links do not count. |
| `colour`, `material`, `size`, `gender`, `age` | a word from that list is not in the row. **When the row fills the field** (`color`, `material`, `size`, `gender`, `age_group`), only that field (and the product type) counts. |
| `claim` | a feature or claim word (organic, vegan, recycled, waterproof, wireless, dishwasher, ...) is not in the row |
| `brand_other` | another product's brand from the same upload, or a well-known brand (Apple, iPhone, Samsung, Nike, ...) the row does not name |
| `brand_missing` | the row has a brand and the title does not |
| `promo`, `price`, `symbols`, `all_caps` | promotional phrases, a currency, `!` or symbols the row does not have, ALL CAPS words |
| `too_long`, `empty`, `unrelated` | over 150 (5000) characters, empty, or no word of the original title or product type |
| `html`, `url` | a description with HTML or a link |

The model sees only the row's descriptive fields (title, description, brand, mpn, colour, size,
material, pattern, gender, age group, product type, category). Never price, id, gtin, links or
unknown columns: custom labels often hold words like "sale".

## Measuring

`_dev/feed-eval.md` in the main repo has the held-out eval (30 synthetic products). For a real
shop the honest test is the supplemental feed on part of the catalogue: apply it to half of a
category, leave the other half, and compare clicks in Merchant Center after several weeks. A
small shop will usually not have enough clicks to tell a real lift from noise; say so.

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `INTERNAL_API_KEY` | — | Required for every endpoint but `/health` (unset → `503`). Also sent to 03. |
| `APPROVER_KEY` | — | Required to approve and revoke. Unset → both refused. |
| `DB_PATH` | `/data/feeds.sqlite` | SQLite file (the uploaded files are kept in it) |
| `FEED_MAX_BYTES` | `10000000` | largest upload |
| `FEED_MAX_ROWS` | `5000` | most products per upload |
| `GATEWAY_URL` | empty | llm-gateway (03). Empty = rule-based titles only. |
| `GATEWAY_TIMEOUT` | `300` | seconds per product |

## Known limits

- **The checks are word lists, in English.** They cannot see a wrong meaning built from true
  words: a colour read as a material ("Brass Table Lamp" when `color` is Brass and `material` is
  Metal), a size read as an age, or a claim the shop's own old title made ("Fast"). Read the
  before/after list before approving.
- **Strict on purpose.** A word from the lists used in another sense is rejected ("small front
  pocket" is not size Small; a "brass buckle" when `material` is Leather). The product then gets
  the rule-based title or keeps its description. Numbers are checked by value, except pack counts:
  "3-Pack", "pack of 3", "3 pcs", "6 pairs" need a count in the row ("set of 3", "6 pairs"), so a
  "3 m" cable can't become a "3-Pack". Other numbers are still checked by value only.
- **Descriptions** are off by default. The model tends to add "perfect for ..." phrases, which are
  not in the promo list.
- **One product per model call.** About 3 to 5 seconds per product with the local writer model
  on a laptop: 5000 products take hours. Run `/propose` in chunks, or use rule-based titles only
  (`"llm": false`).
- **No store connector.** You export the feed from your shop (Shopify, WooCommerce and Merchant
  Center can all download one) and upload it here. UTF-8 only.
- Titles only become better for search if Google agrees. There is no lift measurement here.

## CI

`.github/workflows/ci.yml` runs the tests (respx mocks only; no real call to any service), then
pushes `ghcr.io/<you>/<repo>:latest` on every push to `main`.
