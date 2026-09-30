# Company fixtures v2 (held out) — Phase 1 task bridge

Five more **fictional** businesses, in industries not covered by `../companies/` (v1): logistics,
tutoring, new-build homes, building materials and a beauty clinic. Each has scoped facts, one task and
six pasted chatbot answers, with the labels a careful checker should give. The layout, scoring and
draft kinds are the same as v1. See `../companies/README.md` and `_dev/phase1-contracts.md` §1, §4, §5.

**Everything here is invented.** The companies, people, customers, prices, certificate numbers,
authorisation codes, schemes (such as "HomeStart equity loan") and URLs (`*.example`) do not exist.
Any resemblance to a real business is accidental.

**Held out, written without seeing the checker.** These files were written from the plan, the
contracts, the v1 README, one v1 company (`lake-ember-lodge`) used for format only, and the v1
validator. The author did not read `88-task-bridge/`, `05-brand-service/app/`, any results in
`23-eval-suite/results/` or the other v1 companies. Use this set to measure. Do not tune code to it.
If a checker misses a case, fix the checker in general. Do not edit an expected file to make it pass.

"Today" is **2026-09-29** for every company. Every task publishes in October 2026, after a fact that
is valid today has expired.

## Layout

```
<slug>/facts.yaml       {company, facts: [Fact v2 ...]}  10 facts each: >=1 internal, >=1 restricted
<slug>/task.yaml        {goal, pieces (2-3), scope, publish_on, audience, notes}
<slug>/drafts/01-clean … 06-invented .txt + .expected.yaml   (blocked, findings, must_not_flag, # WHY:)
```

The drafts are written the way free chatbots answer: a preamble, markdown, emojis and a closing
offer. Piece headings vary: `=== 1 LINKEDIN ===`, `**1. LinkedIn**`, `1. LinkedIn`, `1 INSTAGRAM`,
`### Piece 1 – LinkedIn post`, `## Post 2 — Email` and `Instagram:`. Slots appear as `[[key]]`,
`\[\[key\]\]` and `{{key}}`, and some values are typed out (for example "four bedrooms" and
"sixty minutes"). Trap sentences always type the value out or describe it. **Every line that holds a
trap is listed in the expected findings, including subject lines.** Every other line is correct or is
a greeting, heading or sign-off.

## Traps

| Company (type) · task scope · publish | 03 expired at publish (valid today) · already expired | 04 wrong scope | 05 missing disclosure | 06 invented / code differs / forbidden | Internal · restricted |
|---|---|---|---|---|---|
| `northgate-freight` (3PL) · regions=uk-mainland · 10-08 | 11.5% fuel surcharge (ends 10-04) · 4 weeks' free storage (2025), subject line gives only a description | "Saturday delivery at no extra charge" (london-m25 only; non-numeric; body and subject) | £68 without "excl. VAT and fuel surcharge" (body and subject); £25,000 cover without "per consignment" | "most reliable" (no_source); ISO 14001 when only ISO 9001:2015 is held (no_source); AEO-S when the authorisation is AEO-C (conflict; body and subject) | pallet rate floor · Keystone contract rate |
| `brightpath-tutors` (tutoring) · sites=online, segments=gcse · 10-12 | free trial lesson (ends 10-05), **named only by description** ("your first lesson is on us") · £199 summer bootcamp | "free mock exam every term" (Leeds centre only; non-numeric); £46 A-level rate | 87% grade-4 result without "2026 cohort of 212 students" (post, subject, body) | "top-rated", "qualified teacher" (no_source); Level 3 safeguarding when tutors hold Level 2 (conflict); "guarantee(d)" (forbidden_phrase) | tutor pay · Ashdown Academy contract |
| `oakline-homes` (new-build developer) · variants=phase-2 · 10-15 | up to £10,000 towards stamp duty (ends 10-12) · HomeStart equity loan (closed 03-31) | £299,995 Aspen (Phase 1 only; body and "from" subject) | £389,995 without "selected plots, subject to contract" (body and subject); EPC B without "predicted" | "award-winning", "most energy-efficient", "5-star rated" (no_source); EPC A when the rating is B (conflict); 12-year when the warranty is 10-year (conflict) | Rowan minimum price · Brookvale bulk price |
| `granite-and-co` (insulation maker) · segments=trade, variants=firecore-slab · 10-06 | 10% trade discount (ends 10-03) · FireCore G40 grade (discontinued) | BBA Certificate 25/6120 (ThermaBoard's, not FireCore's; subject "now BBA certified"); £24.99 retail price | free delivery over £500 without "within 50 miles of our Stoke depot" (subject and body) | Euroclass A1 when the rating is A2-s1,d0 (conflict); "LPCB approved", "best-value", "4.9 out of 5" (no_source) | FireCore unit cost · Hollins framework price |
| `luma-beauty` (salon/clinic) · sites=richmond · 10-10 | 20% off Glow Facial (ends 10-04) · free brow tint with a lash lift (summer) | £110 Chelsea Glow Facial price (body and subject) | £55 lash lift without "patch test required at least 48 hours before" (post, subject, body); 9-in-10 result without "survey of 120 clients, results vary" | "number one" (no_source); "clinically proven", "permanent results" (forbidden_phrase); Level 5 when Maya holds Level 4 (conflict) | therapist commission · Kewbridge spa rate |

Trap kinds covered:
- certificates with a similar but different identifier: ISO 9001 vs ISO 14001, AEO-C vs AEO-S, BBA vs UKCA/CE, Level 2 vs Level 3, Level 4 vs Level 5;
- codes that differ: Euroclass A2-s1,d0 vs A1, EPC B vs A;
- ratings, awards and superlatives with no source;
- non-numeric inclusions used out of scope: Saturday delivery, the termly mock exam;
- an offer that expires before `publish_on` and is named only by a description (BrightPath);
- missing disclosures in both a subject line and a body sentence (all five companies).

The clean drafts use every scoped credential correctly (ISO 9001:2015 NGF-QMS-4471, AEO-C GBAEOC0042917, Level 2 safeguarding, predicted EPC B, Euroclass A2-s1,d0, Maya's Level 4). They also use the result claims with their disclosures (87% with the cohort, 9 in 10 with the survey). A checker that flags these
lines is giving a false warning.

Values from internal and restricted facts never appear in any draft.

Where a subject line holds two different wrong facts, its expected finding leaves `fact_key` null
(oakline 06, luma 03). A checker may also add a second label to a trap sentence. For example, it may
add `conflict_or_expired` next to `wrong_scope` on the Chelsea price. Under the scoring rule, an extra
finding on an expected sentence does not count against the checker.

## Validate

```bash
python ../companies/validate_fixtures.py .     # run from this folder; stdlib + PyYAML
```

Result when written: `5 companies, 50 facts, 30 drafts, 0 problems`. As a negative control, the
validator was run on a mutated copy. The copy had an internal value added to a clean draft, a
disclosure added to a missing-disclosure draft, and an oakline task scope changed to phase-1. It
reported 9 problems. The validator cannot detect a trap line that is missing from an expected file
when the same disclosure appears elsewhere in the draft. To cover that gap, every line not listed in
the expected files was checked by hand.
