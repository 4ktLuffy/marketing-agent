# prompt-library

Deploy **4 of 53** of the local-LLM marketing agent. It holds the marketing prompts,
one YAML file each, that the LLM gateway (03) runs. They're kept in their own repo so
you can change what the agent writes without redeploying code, and so every prompt
change is reviewed and versioned.

| Prompt | Output | Used by |
|---|---|---|
| `blog_post` | markdown | 25 blog writer |
| `social_posts` | JSON posts per channel | 26 social writer |
| `ad_copy` | JSON headlines ≤30 / descriptions ≤90 | 27 ad copy |
| `email_newsletter` | JSON subject/preheader/body/CTA | 28 email writer |
| `seo_brief` | JSON brief | 29 SEO brief |
| `repurpose` | JSON posts grounded in a source | 30 repurpose |
| `summarize_page` | JSON competitive read | 31 research |
| `keyword_clusters` | JSON clusters by intent | 32 keyword research |
| `kb_answer` | text with citations, or refuses | 34 knowledge-base answer |
| `rewrite_to_fix` | JSON edits + corrected text | 35 quality gate |
| `trend_digest` | markdown | 36 morning digest |
| `competitor_changes` | markdown | 37 competitor watch |
| `content_plan` | JSON week plan | 40 content planner |
| `weekly_report_highlights` | markdown | 41 weekly report |
| `claim_details` | JSON details of one sentence (listed without seeing any facts) | 44 claim checker |
| `detail_check` | JSON: is each detail stated in the facts, and where (quote) | 44 claim checker |

## Where to deploy

It isn't a service. It's **mounted read-only into the gateway container**
(`../04-prompt-library/prompts:/prompts:ro` in `01-marketing-stack`). The gateway
picks up changes on the next call, so deploying it is just:

```bash
cd 04-prompt-library && git pull
```

## Prompt file format

```yaml
name: ad_copy                 # must equal the filename
description: ...              # shown by GET /v1/prompts
output: json                  # text | json
temperature: 0.8
max_chars: 280                # optional, text only: longer output is retried
vars:
  product: required
  offer: optional
system: |                     # Jinja; {{ brand }} is injected by the gateway
  ...
template: |                   # Jinja
  ...
schema: {...}                 # JSON Schema (object root) when output: json
example_vars: {...}           # used by tests and scripts/try_prompt.py
```

## Lessons from testing on qwen2.5:7b (kept as comments in the files)

- **Show only the relevant rules.** When the social prompt listed rules for every
  channel, the model wrote posts for channels nobody asked for. It now renders rules
  only for the requested channels.
- **Leave the brand out of the rewrite prompt.** With the brand profile in context, the
  rewrite prompt replaced the whole draft and swapped the user's link for the brand homepage.
- **Ask for the edits before the text.** Asked for the corrected text alone, the model
  often returned the draft unchanged.
- **Never ask a small model for a yes/no fact verdict.** Asked "is this claim supported?",
  qwen2.5:7b said yes to 10 of 13 invented claims. The claim checker makes it list
  details and quote the evidence, and code makes the decision.
- **List details before showing the facts.** With the facts in view, the model listed the
  facts' details ("medium roast") instead of the claim's ("dark roast").
- **Give instructions, not diagnoses.** "Remove the banned claim "X"" worked 6/6 times;
  "contains banned phrase 'X'" worked 0/6. The quality gate (35) phrases its requests
  as instructions.

## Test

```bash
pip install -r requirements-dev.txt
pytest -q                                  # every prompt loads, renders, has a valid schema
python scripts/try_prompt.py ad_copy       # run one prompt against a live gateway
python scripts/try_prompt.py --all --gateway http://localhost:8103
```

Linting a prompt only proves it loads. To measure output quality, use `23-eval-suite`.

## CI

Runs the lint tests on every push and pull request, so a broken template fails in CI
instead of failing at runtime in the gateway.
