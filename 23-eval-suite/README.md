# eval-suite

Deploy **23 of 53** of the local-LLM marketing agent. It tests the agent's writing against
fixed cases and measures how often the local model produces copy you could actually
publish: within platform limits, free of banned phrases, on the requested channels,
and with no invented statistics.

Run it after you change a prompt (04), swap the model (02) or edit the brand (05).
One run at temperature > 0 tells you little, so every case runs several times and you
get a pass rate.

## Where to deploy

**Your laptop, or a cron job on the stack host.** It needs the gateway (03), the brand
service (05) and platform rules (14) to be reachable. It doesn't run as a service.

## Run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                   # unit tests of the checks (no network)

python -m evalsuite.run                     # all cases x 3, against localhost:81xx
python -m evalsuite.run --only ad_copy_limits --repeats 5
python -m evalsuite.run --min-pass-rate 0.8 # exit 1 below 80% (use in cron/CI)
```

Output:

```
3/3  ad_copy_limits                     avg  11.8s
2/3  social_posts_channels_and_limits   avg  30.2s
      fail platform_ok @posts[*]: x: ['291 chars, limit 280 (over by 11)']
...
pass rate 27/30 = 90%
```

A JSON report with every output goes to `results/`.

## Cases

The cases live in `cases/*.yaml`. Each one is a prompt, fixed variables, and checks:

| Check | Fails when |
|---|---|
| `max_chars` / `min_chars` | any selected value is outside the length |
| `contains` / `not_contains` | a required string is missing / a forbidden one appears |
| `count` | the number of items (e.g. headlines) is out of range |
| `numbers_from_input` | the output contains a number that isn't in the input (an invented stat or price) |
| `brand_ok` | brand service (05) reports an **error** violation |
| `platform_ok` | platform rules (14) reject a post for its channel |
| `channels_match` | posts don't cover exactly the requested channels |

Paths select what to check: `""` means the whole output, `subject`, `headlines[*]`, `posts[*].text`.

## Measuring the claim checker (44)

```bash
python -m evalsuite.claims --checker http://localhost:8144
```

`cases/claims/*.yaml` hold labelled claims (`supported` / `unsupported`) about the example
brand's facts. It reports how many invented claims were caught and how many true ones
were flagged. Both numbers matter. Write your own labelled claims when you replace the
example brand.

## Configuration

| Env / flag | Default |
|---|---|
| `GATEWAY_URL` / `--gateway` | `http://localhost:8103` |
| `BRAND_URL` / `--brand` | `http://localhost:8105` |
| `RULES_URL` / `--rules` | `http://localhost:8114` |

## CI

The GitHub runner only runs the unit tests, because live evals need your Ollama. For
nightly live evals, run it on a self-hosted runner or with cron on the stack host.
