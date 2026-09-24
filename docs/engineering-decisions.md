# Engineering decisions

Seventeen decisions that shaped the agent, each with the evidence that drove it. The evidence
is from testing on `qwen2.5:7b` unless stated. Sources: `CHECKPOINT.md`, `START-HERE.md`,
`BLUEPRINT.md`, `_dev/NIGHT-LOG.md`, and the READMEs of the deploys named.

---

## 1. Never ask a 7B model for a yes/no fact verdict

**Context.** Every draft has to be checked against the brand's approved facts before it
reaches a reviewer. The obvious design is to show the model the facts and the claim and ask
"is this supported?".

**Decision.** The model never gives a verdict. It lists a claim's details, then looks each
detail up and must quote where it is stated. Code checks that the quote really exists in the
evidence and makes the decision. A detail the model skips is asked again on its own; if still
unanswered it counts as unsupported, because an empty answer must never mean "all fine".

**Evidence.** Asked for a yes/no verdict, the model caught 3 of 13 invented claims (it said
"yes" to 10). The list-and-quote design catches 53 of 55 across all labelled sets, and 5 of 6
on held-out real-post sentences (`44-claim-checker/README.md`).

**Trade-off.** Each sentence needs two or more LLM calls (about 2-10 s per sentence on a
laptop), and the checker flags about 1 in 8 true sentences.

## 2. List a claim's details before showing the model the evidence

**Context.** The first detail-listing prompt had the facts in view.

**Decision.** `claim_details` runs without any facts in context. Only the second step,
`detail_check`, sees the evidence.

**Evidence.** From `04-prompt-library/README.md`: with the facts in view, the model listed the facts' details ("medium roast")
instead of the claim's ("dark roast"), so wrong values passed.

**Trade-off.** One extra call, and the model sometimes lists details that are not claims.
Questions, calls to action and moods are now skipped for that reason.

## 3. Give the model instructions, not diagnoses

**Context.** The quality gate (35) asks the model for a minimal rewrite when a draft breaks a
rule.

**Decision.** Problems are phrased as imperative instructions: `Remove the banned claim "X"`.

**Evidence.** From `04-prompt-library/README.md`: the instruction worked 6/6 times. The diagnosis form, `contains banned phrase
'X'`, worked 0/6. The eval case "rewrite removes banned claims" is now 3/3.

**Trade-off.** Every checker's output has to be translated into an instruction; a new kind of
check needs its own phrasing.

## 4. Ask for the edits before the corrected text

**Context.** The rewrite prompt originally asked only for the corrected text.

**Decision.** `rewrite_to_fix` returns a JSON list of edits first, then the corrected text.

**Evidence.** Asked for the corrected text alone, the model often returned the draft
unchanged.

**Trade-off.** More output tokens per rewrite.

## 5. Stage n8n operations instead of letting the HTTP node run them per item

**Context.** Drafting, revising, approving and measuring change the status of several
calendar items.

**Decision.** Workflows 48, 49, 38 and 52 use staged operations so status changes on one post
happen in order.

**Evidence.** n8n's HTTP node runs one request per item in parallel, and batching does not
serialise it, so status changes on the same post raced. Found in Phase 2 end-to-end runs.

**Trade-off.** More nodes per workflow, and slower runs for large campaigns.

## 6. Ask n8n for the full HTTP response on every list call

**Context.** n8n turns a JSON `[]` response into zero items, and a node with zero items stops
the workflow without an error.

**Decision.** All 9 list calls use full responses, and the workflows handle the empty case
explicitly ("All clear", "No matching calendar items.").

**Evidence.** Before the fix, the approval and rules forms would hang with nothing to review,
and the weekly planner would stop in its first week.

**Trade-off.** Every list call has to unwrap the body itself.

## 7. Parse user targets and channels in code

**Context.** The campaign planner prompt (`campaign_plan`) returns a plan with targets and
channels.

**Decision.** Targets the user stated and channel names are parsed and normalised in code;
the model's version of them is not used.

**Evidence.** The model turned "50 clicks" into "signups >= 50" and renamed channels
("Social Media (Twitter)").

**Trade-off.** A small parser to maintain; phrasings it does not recognise are left out
rather than guessed.

## 8. The planner's output is not evidence

**Context.** The fact checker accepts the brief for a piece (`context`) as evidence, so a
claim stated in the brief is allowed.

**Decision.** For campaign drafts, only the user-given goal, offer and audience count as
evidence. The planner's own brief does not. The same rule was later applied to two more
sources: knowledge-base documents written by the trend digest and competitor watch (LLM
summaries of untrusted pages) are excluded from evidence (`KB_UNTRUSTED_SOURCES` in 44), and
the repurpose tool (30) no longer passes the pasted source text as evidence.

**Evidence.** A planner-invented "50% off" was accepted by the fact checker because the
planner's brief had been passed in as evidence. The model was, in effect, citing itself.

**Trade-off.** Drafts can only use facts the user or the brand file supplied, so a sparse
brand file means more flagged drafts.

## 9. Keep the human approval gate in the workflow, not in the prompt

**Context.** An agent that can approve its own drafts can publish anything.

**Decision.** Only the approval form (38), behind n8n Basic Auth, moves an item to
`approved`. The calendar tool (33) refuses to set `approved` or `published`, and refuses to
change items that are already approved, published or rejected. Approved items are locked for
editing. The publisher reads only approved, due items, and does not mark a dry run as
published.

**Evidence.** In the end-to-end chat test, "approve item 2" was refused. In the hosted-model
run (NIGHT-LOG cycle 1) the agent scheduled a rejected item and revived another; both are now
refused.

**Trade-off.** Every post needs a human click, including ones that passed every check. The
gate lives in the n8n workflows: the calendar API accepts `approved` from any caller with the
internal key, and a separate approver key is still open work.

## 10. Route every writing and checking call through the gateway, with schema and retries

**Context.** Small local models drift out of format: extra prose, code fences, `<think>`
blocks, invalid JSON.

**Decision.** The gateway (03) renders named prompts, injects the brand, active rules and
facts, passes a JSON Schema to the model to constrain decoding, validates with `jsonschema`,
and retries with the model's own answer and the exact problem shown back to it. The same
gateway can call any OpenAI-compatible API with `LLM_PROVIDER=openai`.

**Evidence.** Moving writing to a hosted model needs gateway settings only, no workflow
change. On Groq `gpt-oss-120b`, default reasoning effort returned empty JSON for social posts;
`REASONING_EFFORT=low` passed 4/4 (`03-llm-gateway/README.md`). That fix is one gateway
setting. (The chat agent does not go through the gateway; its hosted option is a separate
workflow variant, see decision 16.)

**Trade-off.** One more service in every call path, and a 60 s cache for brand and rules
(which had its own bug, see [bugs-found-by-testing.md](bugs-found-by-testing.md)).

## 11. Keep the local 7B model, measured against alternatives

**Context.** Smaller or newer models could be faster, and a hosted model could be better.

**Decision.** `qwen2.5:7b` stays the default. The hosted path is supported but optional.

**Evidence.** Tool choice: `qwen2.5:7b` 72/72, `qwen3.5:4b` 45/48, `granite4` 43/48 (it
called tools for small talk). Groq `gpt-oss-120b` was better on the v6 fact-check set (6/6
caught, 1/6 true flagged vs 5/6 and 3/6 locally). No tool-choice or drafting-time comparison
with Groq is recorded.

**Trade-off.** Locally the fact checker flags more true sentences, and gateway calls take
2-35 s. The hosted model sends copy and facts to a third party. The measured gap is on small
sets.

## 12. Show only the relevant rules, and keep the brand out of the rewrite prompt

**Context.** Prompts that include everything give a small model more to misread.

**Decision.** The social prompt renders rules only for the requested channels. The rewrite
prompt gets no brand profile, and URLs in a draft are listed and restored in code after a
revision.

**Evidence.** With rules for every channel, the model wrote posts for channels nobody asked
for. With the brand in context, the rewrite prompt replaced the whole draft and swapped the
user's link for the brand homepage (`04-prompt-library/README.md`). The revision prompt (49)
did the same until the draft's URLs were listed in the prompt and restored in code
(`CHECKPOINT.md`, bug 10).

**Trade-off.** Prompt templates carry more conditional logic.

## 13. Reject MiniCheck as the verifier model

**Context.** MiniCheck models are built for exactly this job: checking claims against
evidence.

**Decision.** Not used by default. `VERIFIER_MODEL` lets you point the checking prompts at a
different model after measuring it with `python -m evalsuite.claims`.

**Evidence.** MiniCheck models are licensed CC BY-NC, which rules out commercial use. The
agent is meant for real marketing work.

**Trade-off.** A general 7B model with a list-and-quote pipeline instead of a purpose-trained
checker.

## 14. Integrate Postiz, Umami and Listmonk instead of building them

**Context.** Before Phase 2, 13 open-source marketing agents, about 10 n8n marketing templates
and the self-hostable marketing tools were reviewed.

**Decision.** Build only what nothing else offered: a campaign object with goals and targets
(45) and learning from a reviewer's edits (46). Integrate publishing, analytics and email:
Postiz (about 19 networks), Umami (analytics API) and Listmonk. The Postiz bridge (54), the
Umami sync (55) and its daily schedule (56) are built; Listmonk is not.

**Evidence.** No open-source tool found held a campaign with targets or learned from edits.
Publishing and analytics are mature elsewhere. The rule learning follows
langchain-ai/social-media-agent's reflection step; "set targets before shipping, report what
was never measured" comes from the Kai CMO harness.

**Trade-off.** The bridges depend on external APIs they were only tested against with mocks
(Postiz public API; Umami v3.4.0, checked against its source). The Postiz bridge posts text
only: channels that need media are refused. Without the bridges configured, publishing still
goes to one webhook and analytics come from CSV upload.

## 15. Run the fact checker on its own gateway, so it can use a bigger model

**Context.** Writing and checking have different needs. Writing runs on every draft and can be
local; checking decides what reaches a reviewer, and the local model flagged many true
sentences.

**Decision.** The stack runs a second gateway instance, `llm-gateway-verifier`, and the claim
checker (44) calls only that one. `VERIFIER_PROVIDER` and `VERIFIER_MODEL` choose its model.
The default is the same local model as writing; the documented hybrid keeps writing local and
runs checking on a hosted model.

**Evidence.** On the held-out v6 set, local `qwen2.5:7b` caught 5/6 invented claims and
flagged 3/6 true ones; Groq `gpt-oss-120b` caught 6/6 and flagged 1/6
(`01-marketing-stack/README.md`, NIGHT-LOG).

**Trade-off.** In hybrid mode every draft's sentences and the brand facts go to the hosted
provider, and each sentence costs hosted tokens. That is also why `/verify` now requires the
internal key and caps input size.

## 16. Hosted chat agent as an optional variant, with the local model as fallback

**Context.** The chat agent calls Ollama directly for tool calling, not through the gateway,
so the gateway switch does not move it.

**Decision.** `24-wf-chat-agent/variants/hosted.json` is the same agent on n8n's OpenAI chat
node (Groq `openai/gpt-oss-120b`, `reasoning_effort` low) with the local Ollama model as a
fallback. `import-n8n.sh` imports it instead of the local workflow when `CHAT_PROVIDER=hosted`.

**Evidence.** From NIGHT-LOG cycle 1: Groq's free tier (8k tokens per minute) is reached by one
multi-tool turn, and n8n's OpenAI node throws on a `429` without retrying, so without a
fallback 3 of 3 turns in a burst failed. With the fallback, a turn whose 5th call hit a `429`
was finished by the local model.

**Trade-off.** With the hosted variant, chat messages and tool results (drafts, calendar and
campaign data) go to the provider. A turn that falls back is slower.

## 17. Validate tool arguments in code, and keep reviewed items out of the agent's reach

**Context.** Running the chat agent on a different model changed what it sent to tools.

**Decision.** The calendar tool (33) accepts only a plain item number as an id (`53`, `#53`,
`item 53`) and refuses anything else with a message the model can act on. It refuses
`set_status` and `schedule` on items that are `approved`, `published` or `rejected`.

**Evidence.** From NIGHT-LOG cycle 1: `gpt-oss` sent its own tool-call id (`fc_76a7…`) as the
item id; stripping the letters produced a 20-digit id and a calendar error. After the fix the
model got the refusal and retried with the right number. The same run showed the agent reviving
a rejected item (rejected to draft) to satisfy "I'm the boss, skip the review".

**Trade-off.** A person has to reopen rejected items; the agent can only suggest a new draft.
