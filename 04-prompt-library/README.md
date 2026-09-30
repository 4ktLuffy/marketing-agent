# prompt-library

Deploy **4 of 89** of the local-LLM marketing agent. It holds the marketing prompts,
one YAML file each, that the LLM gateway (03) runs. They're kept in their own repo so
you can change what the agent writes without redeploying code, and so every prompt
change is reviewed and versioned.

| Prompt | Output | Used by |
|---|---|---|
| `blog_post` | markdown | 25 blog writer |
| `social_posts` | JSON posts per channel; optional `customer_phrases` (70 `GET /relevant` → `customer_phrases`) asks it to reuse one customer phrase where it fits, never as a quote; optional `open_with` (one phrase picked in code) requires it word for word in the first sentence of every post, and `open_with_feedback` carries the caller's one retry after a code check (70 README) | 26 social writer |
| `ad_copy` | JSON headlines ≤30 / descriptions ≤90 | 27 ad copy |
| `email_newsletter` | JSON subject/preheader/body/CTA | 28 email writer |
| `seo_brief` | JSON brief | 29 SEO brief (also run weekly for Search Console queries by 69) |
| `repurpose` | JSON posts grounded in a source | 30 repurpose |
| `summarize_page` | JSON competitive read | 31 research |
| `keyword_clusters` | JSON clusters by intent | 32 keyword research |
| `kb_answer` | text with citations, or refuses | 34 knowledge-base answer |
| `rewrite_to_fix` | JSON edits + corrected text | 35 quality gate |
| `trend_digest` | markdown | 36 morning digest |
| `competitor_changes` | markdown; optional `ads` (78's new/changed/stopped ads, exact texts): an ad is quoted exactly or not at all, and 37 removes any quote that is not an exact ad or page text | 37 competitor watch |
| `competitor_brief` | JSON `{what_changed, decision: match\|counter\|ignore, reasons 1–3, response_points ≤ 3}` for a changed pricing page, grounded in the approved facts; 37 drops reasons/points with a number not in the diff or the facts | 37 competitor watch (saved as a `competitor_brief` calendar idea) |
| `competitor_key_pages` | JSON `{pages ≤ 5: {url, type: pricing\|product\|features\|about}}` chosen from the homepage's links; 81 keeps only listed, same-domain, readable URLs | 81 track competitor |
| `content_plan` | JSON week plan | 40 content planner |
| `weekly_report_highlights` | markdown | 41 weekly report |
| `weekly_actions` | JSON `{headline, what_changed ≤ 4, actions ≤ 5: {channel, action ≤ 140, why}}`, one action per channel; the workflow drops any action whose `why` cites a number not in the data | 41 weekly report |
| `client_report_summary` | JSON `{summary: 1–5 sentences ≤ 240}` for the top of a monthly client report, from finished facts and the work log; causes only hedged ("may have", "we can't tell yet"). 85 drops every sentence with a number not in the data and every unhedged causal claim, and lists them in the reviewer notes only | 85 monthly client report |
| `feed_title` | JSON `{title ≤ 150, description}` for one product of a Google Merchant Center feed, from that product's own fields only (no price, id, gtin, links): brand, product, then colour/size/material first; description only when `with_description` is set. 87 rejects a title with a number, colour, material, size, gender, claim or brand the row does not have, promo text or ALL CAPS, and uses its rule-based title instead | 87 feed optimizer |
| `newsletter_issue` | JSON `{subject ≤ 60, preheader ≤ 90, intro, sections: {title, summary ≤ 280, link}, cta_text, cta_url}` from the week's published items; links must be copied from the items (the workflow blanks any other) | 66 weekly newsletter |
| `experiment_proposals` | JSON `{proposals ≤ 2: {hypothesis, variable, channel, arm_a, arm_b, brief_a?, brief_b?, evidence}}`: one-variable, two-arm tests grounded in the click data and past results (no repeats; a past winner may be proposed once more to replicate it). The workflow drops a proposal whose evidence cites a number not in the data; the stats decide, never the model | 74 experiment manager |
| `claim_details` | JSON details of one sentence (listed without seeing any facts) | 44 claim checker |
| `detail_check` | JSON: is each detail stated in the facts, and where (quote) | 44 claim checker |
| `detail_entails` | JSON `{implied, quote}`: second chance for one detail found not stated, against the 1–3 closest fact lines (44 accepts a yes only with a real quote holding all the detail's values) | 44 claim checker |
| `video_script` | JSON hook, beats (spoken / on-screen / shot), CTA, caption | 57 content formats |
| `landing_page` | JSON hero, benefits, social proof (facts only), answer-first FAQ, CTA | 57 content formats |
| `email_sequence` | JSON 3–5 emails: day, subject, preview, body, CTA | 57 content formats |
| `review_reply` | JSON `needs_human`, `reason`, reply ≤ 600 chars (no invented offers, never asks for a better rating) | no workflow yet; vars come from 58 `GET /reviews/{id}/reply-context` |
| `voice_profile` | JSON voice profile from the 10 interview answers: summary, checkable do/don't rules, words to use/avoid, sentence style, 3 sample lines | stored with 05 `PUT /voice`; 05 adds it to every prompt's `{{ brand }}` |
| `voice_judge` | JSON `{reason, closer: 1\|2}`: which of two posts is closer to a voice | 23 `evalsuite.voice_ab` (blind, both orders) |
| `x_thread` | JSON `{hook_style, posts: 3–7 strings ≤ 270}`: hook post first, not numbered (the workflow adds "1/N") | 65 engine drafter (`thread` slots) |
| `carousel_text` | JSON `{hook_style, slides: 5–8 {title ≤ 40, body ≤ 160}, caption ≤ 300, cta}`: cover slide is the hook, last slide the CTA | 65 engine drafter (`carousel_text` slots) |
| `pillar_atoms` | JSON 15–30 atoms `{kind, text ≤ 280, promo, evidence}` from one pillar, ≥ 4 kinds | 61 content engine: stored with `POST /pillars/{id}/atoms` after each atom is checked by 44 |
| `content_refresh` | JSON `{diagnosis ≤ 300, changes 3–8: {type, fact, where, what ≤ 400, why}, new_title ≤ 60, new_meta ≤ 155, faq ≤ 4}` for a page losing Google clicks; the workflow drops changes whose `why` has a number not in the input or cites no query, and `update_fact` without an approved fact label | 68 content refresh |
| `customer_themes` | JSON themes `{name, kind: pain\|desire\|objection\|trigger\|outcome\|word_choice, quotes: {source_id, quote}}` from one batch of customer snippets; 70 drops every quote that is not an exact part of the snippet it cites, and every theme left with < 2 valid quotes from 2 sources | 70 customer language (`POST /mine`) |
| `positioning_themes` | JSON `{claims: {theme, source_id, quote}}`: one company's messaging claims sorted into the given theme keys (price, freshness, ...), from its page snapshots (09), active ads (78) or, for us, the approved facts (05); no `{{ brand }}`. 78 drops unknown themes/sources and every quote that is not an exact part of the source it cites | 78 ad-library-sync (`POST /positioning/build`, monthly from 37) |
| `customer_headlines` | JSON `{headlines: {phrase_id, text}}`: each reuses one given customer phrase word for word; 70 drops headlines without a given phrase, with a number not in the phrases/facts, or with quotation marks | 70 customer language (`POST /headlines`) |
| `customer_personas` | JSON 2–4 personas `{label, goals, pains, objections, words_they_use}`, every item `{text, cites}` with theme/quote ids; 70 drops uncited items, non-verbatim `words_they_use`, ages and numbers not in the quotes | 70 customer language (`POST /personas`) |
| `voc_judge` | JSON `{reason, closer: 1\|2}`: which of two posts sounds more like someone who knows these customers (given their quotes) | 23 `evalsuite.voc_ab` (blind, both orders) |
| `clip_scoring` | JSON `{scores: {id, hook, standalone, payoff, quotable (0-10), title ≤ 100, hook_line, reason}}` for a batch of transcript windows of one long video; it only judges the given words. 73 ignores unknown ids, clamps scores, replaces a `hook_line` not copied word for word from its window and a title with a number the window does not say | 73 clip finder (default; topic-segment windows, in batches of 6) |
| `clip_ranking` | JSON `{ranking: [ids, best first], notes?: [{id, hook, standalone, payoff, quotable (short notes), title, hook_line, reason}]}`: compares a batch of transcript windows of one long video with each other; `notes` (var) only on the first round. 73 ignores unknown and repeated ids, puts left-out ones last in its own feature order, and checks titles and hook lines as for `clip_scoring` | 73 clip finder with `SCORING_MODE=rank` (a tournament over batches of ~6) |
| `site_answer` | JSON `{covered, answer ≤ 600, sources: [n], buying_intent}`: a website visitor's message answered only from the numbered sources (06 excerpts + 05 facts); "I don't know — let me get a person." when not covered. 79 does not trust it: code drops sentences with numbers, dates, offer, health or legal words not in the sources, promises and "I am human", then 44 checks the rest against the same sources; nothing left means a person takes over | 79 site assistant (`POST /chat`) |
| `lead_enrich` | JSON `{industry, sells, facts ≤ 6: {text, quote, source_url}, size_hints ≤ 3: {statement, quote, source_url}}` from an inbound lead's own homepage/about page only; nothing inferred. 80 drops every fact or size hint whose quote is not on the page, then every statement the claim checker (44, page text as context) does not support | 80 lead hub (enrichment) |
| `lead_first_reply` | JSON `{needs_human, reason, subject ≤ 70, reply ≤ 1000}`: a first reply to an inbound lead's own message from approved facts, with one next step (the booking link); inbound and consented only. 80 removes sentences 44 flags; a person approves and sends it | 80 lead hub (reply draft, channel `lead_reply`) |
| `visibility_questions` | JSON `{questions: [{text, kind: category\|comparison\|problem\|branded}]}`: 15–30 questions buyers type into AI assistants, from the brand's products and audience; only `branded_count` of them name the brand (unaided visibility vs accuracy). 82 re-labels a question naming the brand as branded, drops one naming a competitor and duplicates; a person edits and approves the set | 82 ai-visibility (`POST /questions/generate`) |
| `propose_facts` | JSON `{facts ≤ 40: {text, subject: {kind, ref}, fact_type, attribute, value_text, value?, unit?, currency?, basis?, scope_hints: {sites, regions, channels, segments, plan_tiers, variants}, valid_from?, valid_to?, conditions_text?, required_disclosure_hint?, source_quote}, questions ≤ 15: {text, source_quote}}` from one chunk (~6,000 characters) of the business's own website, brochure or price list; `known_facts` (public ones only) are skipped. Only stated facts, never an inferred price or date, scope words kept ("per person", "weekdays only", "at our Leeds branch"). 72 drops every fact whose quote is not in the source, whose value has a number the quote does not, and every date the quote does not state; the rest become drafts only when the owner ticks them, and each is confirmed on the Facts page | 72 control room, "Set up from your website or documents" |

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
system: |                     # Jinja; {{ brand }} is injected by the gateway;
                              # so is {{ facts }} (numbered approved facts) if declared in vars
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
- **Give the writer the approved facts, and ban numbers that aren't in them.** With only
  the brand summary, writing prompts invented tasting notes ("citrus and honey",
  "chocolate and caramel" for an Ethiopian light roast) and stray numbers. The gateway now
  injects `{{ facts }}` into the writing prompts, which state that no numbers, dates or
  statistics may appear unless they are in the facts or the input. Measured on qwen2.5:7b
  (3 runs per case): the blog case went from 0/3 to 3/3 on invented numbers, and invented
  flavour words across the writing cases fell from 23 to 6. It did not fix a question the
  facts can't answer: asked what the decaf tastes like, the model still gave it the Desk
  Blend's chocolate-hazelnut notes in most runs (clean in 2/8 runs before, 1/8 after), and
  adding "never move a detail from one product to another" changed nothing (0/5), so that
  line was not kept. That gap needs a fact in the brand file or a code check, not more prompt.
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
