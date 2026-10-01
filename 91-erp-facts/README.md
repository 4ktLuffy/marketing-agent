# erp-facts

Deploy **91 of 91** of the local-LLM marketing agent. Chatbots do not know today's prices; the
ERP does. This service reads a business's ERP **read-only** (Odoo first, XML-RPC over HTTPS),
turns configured records into **draft** scoped facts in the brand service (05), and reports
**drift**: a value in the ERP that differs from the fact 05 serves. The owner re-confirms in 05,
and the task bridge (88) reconcile pulls posts that used the stale value back.

It needs no model. It holds no key that can confirm anything.

## Endpoints

All need `X-API-Key: $INTERNAL_API_KEY` except `/health`.

| Method | Path | What |
|---|---|---|
| GET | `/health` | `{ok, mappings, erp_configured, sync_every_min}` |
| POST | `/sync?mapping=<name>&dry_run=true` | Read the ERP, build facts, compare with 05 (`GET /facts/v2`). `dry_run` defaults to **true**. `mapping` omitted = all |
| GET | `/drift?mapping=` | The same comparison as a dry run; never writes |

Response:
`{dry_run, mappings, new:[{key, erp_value, currency}], changed:[{key, served_value, erp_value, erp_write_date}], unchanged:n, pending:[key], skipped:[{record, reason}], errors:[...], written:{created, updated}}`

- `new`: no such key in 05. With `dry_run=false` it is created as a **draft** (`POST /facts/v2`).
- `changed`: the value or currency in the ERP differs from the fact 05 serves. With `dry_run=false`
  it is sent as a **new draft version** (`PUT /facts/v2/{key}`, `source.ref` = `Odoo <model> #<id>
  write_date <...>`). The confirmed version keeps being served until a person confirms.
- `pending`: a newer draft already holds the ERP value; waiting for the owner, not rewritten.
- `skipped`: placeholder price (`skip_if`), empty value or currency, bad key, duplicate key, or a
  fact retired in 05. Reported, never guessed.

`SYNC_EVERY_MIN` (default 0 = off) runs the same real sync in a loop inside the service.

Why `POST /facts/v2` and not `/facts/v2/import`: 05's import needs the owner key, which this
service must never hold. Creating a new key as a draft and PUTting a new draft version need only
the internal key.

## Safety

1. **Read-only ERP.** `app/odoo.py` has one allowlist, `ALLOWED_METHODS`: `authenticate`,
   `version`, `search_read`, `read`, `search`, `search_count`, `fields_get`. Any other method
   (write, create, unlink, call_kw, execute, ...) raises `OdooBlocked` before any network call,
   and the client has no method that could send one.
2. **Credentials only from env or file**: `ODOO_URL`, `ODOO_DB`, `ODOO_LOGIN`, `ODOO_KEY`, or
   `ODOO_ENV_FILE` pointing at a file with those lines (mount it read-only). Use an API key of a
   read-only Odoo user. They are never logged, never returned by any endpoint, errors are scrubbed,
   and `repr`/`str` of the client masks them. Plain `http://` is refused except for localhost.
3. **No personal data becomes a fact.** A mapping reads only the fields it lists (plus `id` and
   `write_date`). Models `res.partner`, `res.users`, `sale.order`, `hr.*`, `mail.*`,
   `account.move*` are refused at load time and again in the client, unless named under
   `allow_models`. Fields and domain terms whose names look personal (partner, customer,
   employee, user, email, phone, vat, street, ...) are refused, and dotted paths are not allowed.
4. **Drafts only.** It never confirms, retires or imports, and never sends `X-Owner-Key`. It
   **refuses to start** if `FACT_OWNER_KEY`, `APPROVER_KEY` or any `*_OWNER_KEY` is in its
   environment. Facts default to `sensitivity: internal` unless a mapping says `public`.
5. A fact that equals the ERP value is left alone, so re-running is a no-op.

The tests in `tests/test_safety.py` prove each point against a fake Odoo (real XML-RPC on a local
socket) and a fake 05.

## Mapping file

`MAPPINGS_FILE` (YAML, see `config/mappings.example.yaml`). Per mapping: `name`, `model`,
`domain`, `fields`, `key_template` (`{field}` or `{field|slug}`, e.g. `rate-{name|slug}-single`),
`subject {kind, ref_template}`, `fact_type`, `attribute`, `value_field`, `currency` (fixed) or
`currency_field` (many2one to res.currency), `unit`, `basis`, `conditions`, `scope` (fixed),
`scope_fields` (e.g. `sites: hotel_id`), `sensitivity` (`public`|`internal`), `text_template`,
`text_variants` (used when derived values exist), `derive` (regex from a field), `value_text_template`,
`required_disclosures`, `skip_if` (e.g. `value <= 1` = placeholder price). `outputs:` lets one
mapping make several facts per record, each overriding the fields above. A bad mapping stops the
service at start with every problem listed.

### Example 1: hotel
`product.template` where `is_room_type = True`. `hotel_rack_rate_single/double` become **public**
facts, `hotel_tour_rate_single/double` **internal** ones (88 only ever slots those). Currency comes
from `hotel_rate_currency_id`, the room view from `x_room_view_type`, and `hotel_id` becomes
`scope.sites`. Keys: `rate-deluxe-lake-rack-single`, `...-tour-double`, ...

### Example 2: beer distributor
`product.template` with `company_id = 3` and `sale_ok = True`: `list_price` per crate in ETB,
key `price-harer-33cl-crate`. A name such as `St George 24x33cl` also yields "24 bottles of 33cl"
in the text; `Harer 33cl` has none and gets the plain text. A price of 1 or less is skipped.

## Run

```
cp .env.example .env     # edit; never commit it
pip install -r requirements-dev.txt && pytest -q
python -m app            # listens on 0.0.0.0:8000 (PORT)
```
Docker: the image runs as a non-root user with a healthcheck and ships only the example mapping;
mount your own and set `MAPPINGS_FILE`.

## Known limits

- Odoo only; one ERP per instance. Drift compares value and currency, not wording.
- Odoo returns `false` for an empty field; it is treated as "no value".
- Not in `01-marketing-stack` compose yet.

## CI

`.github/workflows/ci.yml` runs `pytest` on every push and pull request, and on `main` builds and
pushes the image to GHCR.
