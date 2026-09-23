# Checkpoint: Phase 2 (campaigns + learning), 2026-09-23

Nothing has been deployed, pushed or committed. Everything is local files.

## State in one line

53 deploys exist. All unit tests pass and the compose file validates (24 services).
Phase 2 is **built and verified end to end**, including the approval form, rewrites,
learning, publishing and measurement (resumed session, see "Resumed" below).

## Verified end to end (local n8n 2.40.5 + real qwen2.5:7b)

| Area | Result |
|---|---|
| Claim checker (44), 113 labelled claims | caught 53/55 invented, flagged 7/58 true; realistic post sentences: 5/6, 0/9 |
| Plan a campaign (47) | campaign created with targets, dated pieces added to calendar, drafter started |
| Campaign drafter (48) | 7/7 drafts reached `in_review` (after the fixes below); one with an unsupported claim held as a draft |
| Campaigns tool (53) | list, activate (blocked without target), set target, scorecard, unknown campaign |
| Tool selection (23 `evalsuite.tools`) | 11 tools: 88%; 13 tools: 96%; after the knowledge-base description fix: **100% (72/72)** |
| Content eval (23 `evalsuite.run`), 14 cases × 3 | 35/42 = 83%. Learning cases 12/12. Original 10 cases 23/30, down from 26/30 but within 3-run noise. Failures: invented numbers in the bare blog prompt, link dropped by the bare social prompt (the workflow restores it), KB answer omitting "30" |
| New prompts | campaign_plan, reflect_rule, revise_with_feedback run on the real model |
| Learning service (46) | live /reflect turned 2 rejections into "Avoid exclamation points in calls to action" |

## Bugs found and fixed during Phase 2 testing

1. Calendar (19) and analytics (20) migrations crashed when two requests arrived at once. Fixed with a lock and a transaction; a regression test fails 3/3 on the old code.
2. `import-n8n.sh` pattern `[2-4][0-9]-wf-*` skipped workflows 50–53. Now `[0-9][0-9]-wf-*`.
3. n8n's HTTP node runs one request per item in parallel (batching doesn't serialise), so status changes on one post raced. Fixed with staged operations (48, 49, 38, 52).
4. Planner-invented "50% off" was accepted because the planner's brief was passed as evidence. Now only user-given goal, offer and audience count as evidence.
5. The model changed a user target ("50 clicks" became "signups ≥ 50"). User targets are now parsed in code.
6. The model renamed channels ("Social Media (Twitter)"). Channels are now normalised in code.
7. The brand one-liner wasn't counted as a fact. It is now.
8. Eval tool cases broke the content-eval loader. Moved to `cases/tools/`.
9. Ollama rejected the schema grammar (`\d`, `exclusiveMinimum`) in campaign_plan. Fixed.
10. Revision prompt swapped the draft's link for the brand homepage. URLs are now listed and restored in code.

## Resumed: verified end to end

| Flow | Result |
|---|---|
| Approval form v2 (38) | approve (scheduled), edit & approve (logged as `edited`), reject → rewrite, drop, back to draft; all in the calendar and learning log |
| Automatic rewrite (49) | invented "50% off" post rejected with a reason and back in review 22 s later: discount removed, free US shipping added, tracked link kept |
| Learning (46 → 51 → 03) | style edit → rule "Never open with a question." → kept in the rules form → gateway fetches it for every prompt |
| Rule reflection quality | 20/20 correct after a prompt fix (style → rule; fact fixes → no rule), stored as eval cases in `23-eval-suite/cases/learning.yaml` |
| Publisher (39) with campaign posts | short link created and saved (`short_url`), carrying campaign UTM tags + `utm_content=<item id>` |
| Measurement (52) | 3 simulated clicks → scorecard `clicks 3/100`; daily summary posted to the webhook |
| Planner (40) | reads clicks per channel and top posts from `/insights` |
| Social writer (26) | gets 3 approved examples (edited ones first) |
| Empty lists | rules form "All clear", calendar tool "No matching calendar items." |

## More bugs found and fixed after resuming

11. **An empty list stopped a workflow silently**: n8n turns `[]` into zero items. The approval and rules forms would hang with nothing to review, and the planner would stop in its first week. All 9 list calls now use full responses.
12. **Campaign posts were published without a short link**, because their `link` field was empty, so campaign clicks would always read 0. The publisher now uses the URL in the text.
13. **A reviewer's plain pasted link lost the campaign tags.** The publisher now fills in missing UTM tags.
14. **A rejected fact fix ("no 50% discount") became a rule banning all discounts.** The reflection prompt now separates style from facts.
15. The approval form now tolerates a field submitted twice.

## Deployment readiness (no Docker on the build machine)

- Image simulation: all 22 services install from their runtime `requirements.txt` alone and start healthy.
- New `01-marketing-stack/scripts/preflight.sh` (before `up`) and `smoke-test.sh` (after import).
- Fixed: the stack scripts sourced `.env` with bash, which breaks on values with spaces (`LISTENING_QUERY=coffee subscription`). They now parse it like docker compose.
- Still untested: an actual `docker compose up`. Run preflight, then compose, then import, then the smoke test.

## Known quality limits seen in real drafts

- The 7B model adds odd emojis (🩸, 🍒) and vague hype ("expert roasters who hand-roast").
- Some invented details pass the checker (about 1 in 25).
- The fact checker flags about 1 in 8 true sentences; those posts go to you as drafts.

## Next after Phase 2 (from the research)

Integrate instead of build: **Postiz** (publishing), **Umami** (analytics API instead of CSV),
**Listmonk** (email). Measure `qwen3:8b` against the current model for tool calling.

## Files for continuing (NOT deploys, don't push to GitHub)

- `_dev/workflow-generator/`: the Python that generates every `NN-wf-*/workflow.json`
  (`python build.py`). **Edit this, not the JSON files**, then rebuild.
- `_dev/local-test/`: scripts used to run everything without Docker. They contain local-only
  test values and paths into a temporary scratchpad directory. `_dev/` is kept out of the
  repository (see `.gitignore`).
