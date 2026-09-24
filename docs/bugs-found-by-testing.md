# Bugs found by testing

Real bugs found while testing the agent, grouped by where they lived. Each was fixed. The
list is from `CHECKPOINT.md` (numbered items 1-15 keep their numbers there) and
`_dev/NIGHT-LOG.md` (items N1-N4, found by running the chat agent on a hosted model), plus the
model and prompt failures found by the eval suite and the hosted-model comparison.

"How it was caught" names the kind of test. Where the build log only says the bug was found
during Phase 2 testing, that is what the table says; Phase 2 testing ran the workflows end to
end in a local n8n 2.40.5 against the real `qwen2.5:7b`.

## Summary

| Category | Bugs |
|---|---|
| Concurrency and n8n execution semantics | 4 |
| Fact evidence and model output trusted where it should not be | 6 |
| Tracking links and publishing | 2 |
| Startup, configuration and scripts | 5 |
| Agent tool input and reviewed items (hosted-model run) | 4 |
| Test harness | 1 |
| **Code bugs, total** | **22** |
| Prompt and model behaviour found by testing (not counted above) | 7 |

## Concurrency and n8n execution semantics

| # | Bug | Effect | How it was caught | Fix |
|---|---|---|---|---|
| 1 | Calendar (19) and analytics (20) migrations crashed when two requests arrived at once | Service crashed | Phase 2 testing; a regression test fails 3/3 on the old code | Lock plus transaction around the migration |
| 3 | n8n's HTTP node runs one request per item in parallel, and batching does not serialise it | Status changes on the same post raced | Phase 2 end-to-end runs of 48, 49, 38, 52 | Staged operations in those four workflows |
| 11 | n8n turns an empty JSON list into zero items, which stops a workflow silently | Approval and rules forms would hang with nothing to review; the planner would stop in its first week | Empty-list checks after resuming ("All clear", "No matching calendar items." now verified) | All 9 list calls use full responses |
| 15 | The approval form could receive a field submitted twice | Not described in the build log | Phase 2 testing after resuming | Form tolerates a duplicated field |

## Fact evidence and model output trusted where it should not be

| # | Bug | Effect | How it was caught | Fix |
|---|---|---|---|---|
| 4 | The planner's brief was passed to the fact checker as evidence | A planner-invented "50% off" was accepted | Phase 2 end-to-end campaign run | Only the user-given goal, offer and audience count as evidence |
| 5 | The model rewrote a user target | "50 clicks" became "signups >= 50" | Phase 2 end-to-end campaign run | User targets parsed in code |
| 6 | The model renamed channels | "Social Media (Twitter)" instead of the requested channel | Phase 2 end-to-end campaign run | Channels normalised in code |
| 10 | The revision prompt swapped the draft's link for the brand homepage | Tracked link lost on rewrite | Phase 2 testing of the rewrite flow | URLs listed and restored in code |
| 7 | The brand one-liner was not counted as a fact | The one-liner's statements did not count as evidence | Phase 2 testing | One-liner included in `/facts` |
| 14 | A rejected fact fix ("no 50% discount") became a rule banning all discounts | The learning loop would over-generalise a one-off correction | Phase 2 testing after resuming (learning flow) | Reflection prompt separates style from facts; 20/20 correct afterwards, stored as eval cases |

## Tracking links and publishing

| # | Bug | Effect | How it was caught | Fix |
|---|---|---|---|---|
| 12 | Campaign posts were published without a short link, because their `link` field was empty | Campaign clicks would always read 0 | Phase 2 testing after resuming (publisher 39 with campaign posts) | Publisher uses the URL in the post text |
| 13 | A reviewer's plain pasted link lost the campaign tags | Clicks not attributed to the campaign | Phase 2 testing after resuming | Publisher fills in missing UTM tags |

With the Postiz bridge (54), a dry run answers HTTP 200 without posting. The publisher (39)
checks for `status: "dry_run"` and leaves such items `approved` instead of marking them
published (NIGHT-LOG: "Postiz dry-run handling in 39").

## Startup, configuration and scripts

| # | Bug | Effect | How it was caught | Fix |
|---|---|---|---|---|
| CI | The gateway's brand and learned-rules cache started at timestamp 0 | On a machine booted less than 60 s earlier (a CI runner, or a server after reboot), prompts went out **without the brand profile** for the first minute | **CI**, after the first push | Cache start fixed; a regression test fails on the old code |
| 2 | `import-n8n.sh` matched `[2-4][0-9]-wf-*` | Workflows 50-53 were never imported | Phase 2 testing | Pattern is now `[0-9][0-9]-wf-*` |
| 9 | Ollama rejected the `campaign_plan` schema grammar (`\d`, `exclusiveMinimum`) | Campaign planning failed | Running the new prompt on the real model | Schema rewritten without those constructs |
| - | Stack scripts sourced `.env` with bash | Broke on values with spaces (`LISTENING_QUERY=coffee subscription`) | Deployment-readiness testing | `.env` parsed the way docker compose parses it |
| - | Shell scripts continued after a failed `cd` | Commands could run in the wrong directory | shellcheck (run locally via `shellcheck-py`) | `cd` exits on failure; shellcheck clean |

## Agent tool input and reviewed items (hosted-model run)

Found in NIGHT-LOG cycle 1, when the chat agent ran on Groq `openai/gpt-oss-120b` in a local
n8n. A different model sent different tool arguments, which exposed these.

| # | Bug | Effect | How it was caught | Fix |
|---|---|---|---|---|
| N1 | The calendar tool (33) stripped letters from the id; `gpt-oss` sent its own tool-call id (`fc_76a7…`) | A 20-digit bogus id, and a calendar 500 | Hosted chat run | Only `53`, `#53` or `item 53` accepted; anything else is refused with a message. Verified: the model read the refusal and retried with 53 |
| N2 | The calendar (19) raised `OverflowError` on ids of 2^63 and above | HTTP 500 instead of 404 | Same run | Now 404; test added, and the old `main.py` fails it (negative control). 116 tests pass |
| N3 | The calendar tool allowed scheduling a **rejected** item, and the agent revived a rejected item (rejected to draft) when told "I'm the boss, skip the review" | A reviewer's rejection could be undone by the agent. Approval was never possible | Same run | `set_status` and `schedule` on rejected items are refused; only a person can reopen them |
| N4 | The calendar tool printed `#undefined` on HTTP errors | Unreadable answer to the agent | Same run | "Calendar error (HTTP n)" |

## Security fixes

These came from a review of the code, not from a failing test, so they are not counted as bugs
above. They are listed in NIGHT-LOG, except the claim-checker input caps, which are in
`_dev/MORNING-REPORT.md` and `44-claim-checker/README.md`.

| Fix | Where |
|---|---|
| Approval, knowledge-base and rules forms now require n8n Basic Auth (`FORMS_USER` / `FORMS_PASSWORD`) | 38, 42, 51, `import-n8n.sh` |
| The calendar tool cannot change approved, published or rejected items | 33 |
| Fact laundering: LLM summaries of untrusted pages in the knowledge base no longer count as evidence, and the repurpose tool no longer passes its source text as evidence | 44 (`KB_UNTRUSTED_SOURCES`), 30 |
| SSRF guard bypass through IPv6 addresses that embed an IPv4 address (IPv4-mapped, 6to4, NAT64, IPv4-compatible) fixed, with a negative control | 07, 08, 09, 12 (`app/net.py`) |
| `X-API-Key` required on 08 `/poll`, 09 `/check` and 44 `/verify` | 08, 09, 44 |
| Input caps on the claim checker (`MAX_TEXT_CHARS`, `MAX_SENTENCES`, `extra_facts` count and length) | 44 |
| No repo mount in the n8n container; the preflight key and hosted chat credentials are passed over stdin | 01 |

Still open (NIGHT-BACKLOG): an API key on the gateway's `/v1/run`, and a separate approver key
so only the approval form can set `approved` at the calendar API.

## Prompt and model behaviour found by testing

These were not code bugs but reproducible model failures, measured and then fixed in the prompt
or in code.

| Failure | Measured | How it was caught | Fix |
|---|---|---|---|
| Rewrite ignored diagnosis-style requests | 0/6 with "contains banned phrase 'X'" | Content eval, rewrite case | Imperative instructions: 6/6 |
| Rewrite returned the draft unchanged | often | Testing on qwen2.5:7b | Ask for the edits before the text |
| Social prompt wrote posts for channels nobody asked for | observed | Testing on qwen2.5:7b | Render rules only for requested channels |
| Fact verdict accepted invented claims | caught 3/13 | Claim checker eval, labelled set | List-and-quote pipeline; code decides |
| Detail listing copied the facts' details instead of the claim's | "medium roast" listed for "dark roast" | Testing the claim checker | List details before showing evidence |
| Chat agent chose the wrong tool after 2 tools were added | 96% with 13 tools | Tool selection eval | Sharpened the knowledge-base tool's description: 72/72 |
| After the repurpose and social tool descriptions changed, "Instagram caption" requests got no tool call | 70/72 | Tool selection eval | No fix recorded yet. Held-out set v2 (20 requests) written before the next description change |

Found in the hosted-model comparison: Groq `gpt-oss-120b` at default reasoning effort returned
**empty JSON** for social posts. At `reasoning_effort=low` it passed 4/4
(`03-llm-gateway/README.md`). The gateway now has a `REASONING_EFFORT` setting.

Also found with the hosted chat agent: n8n's OpenAI chat node throws on a `429` without
retrying, and Groq's free tier (8k tokens per minute) is reached by one multi-tool turn, so 3 of
3 turns in a burst failed. The hosted variant of 24 now falls back to the local model.

## Test harness

| # | Bug | Effect | How it was caught | Fix |
|---|---|---|---|---|
| 8 | Tool-selection cases were stored with content cases | The content-eval loader broke | Running the eval suite | Moved to `cases/tools/` |

## Negative controls that proved a fix

- Migration race (1): regression test fails 3/3 on the old code.
- Gateway cache (CI): regression test fails on the old code.
- Calendar overflow (N2): the new test fails on the old `main.py`.
- SSRF IPv6 bypass: fixed with a negative control.
- Emoji whitelist: the quality gate removed a non-whitelisted emoji from a real draft 3/3
  times.
- Fact checker: every labelled set includes true claims, so a fix that makes it flag
  everything shows up as "true claims flagged".
