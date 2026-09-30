# Company fixtures v10 (held out) — Phase 1 task bridge

Five **fictional** businesses from industries that no earlier set used. Each has ten scoped facts,
one task with three pieces, and six pasted chatbot answers with the labels a careful checker should
give. They test the Phase 1 path (facts in 05 → task pack from 88 → pasted answer → evidence labels)
with **zero model calls**.

**Everything here is invented.** The companies, towns, people, clients, prices, certificate numbers
and contract terms do not exist. Real scheme names (DVSA, RCVS Practice Standards Scheme, FSC,
ISO 9001, RoSPA, British Council) appear only as the kind of credential a small business might hold
or wrongly claim. They say nothing about any real organisation. Any resemblance to a real business
is accidental.

**Held out.** This set was written from `_dev/phase1-contracts.md` (§1, §4, §5), the v1 README and
the v1 fixtures (for format) only. Nobody read `88-task-bridge/`, `05-brand-service/app/`,
`44-claim-checker/`, `23-eval-suite/evalsuite/`, any results file or the sets `companies-v2` …
`companies-v9` while writing it. Whoever builds or tunes the checker must not read these files or
tune code to them. If a checker misses a case, fix the checker in general. Do not add a special rule
for one sentence here, and do not edit an expected file so that it passes.

"Today" for every company is **2026-09-29**. Every task publishes in October 2026, after at least one
fact that is valid today has expired.

## Layout

```
<slug>/facts.yaml        {company: {name, type, today}, facts: [10 × Fact v2]}     (contracts §1)
<slug>/task.yaml         {goal, pieces (3), scope, publish_on, audience, notes}     (POST /tasks, §4)
<slug>/drafts/NN-name.txt             the pasted chatbot answer
<slug>/drafts/NN-name.expected.yaml   {blocked, findings[{contains,label,fact_key}], must_not_flag}  (§5)
```

The six drafts are the same kinds as in v1:

| Draft | What it does | Expected |
|---|---|---|
| `01-clean` | correct, in scope, all disclosures present | `blocked: false`, no blocking finding |
| `02-paraphrase` | the same facts in everyday wording, disclosures in other words, no new numbers (negative control) | `blocked: false` |
| `03-expired` | uses a fact that expires before `publish_on` (and one already expired) | `conflict_or_expired` |
| `04-wrong-scope` | uses a true fact from another site, plan tier or variant | `wrong_scope` |
| `05-missing-disclosure` | uses a fact but leaves out its required disclosure or condition | `missing_disclosure` |
| `06-invented` | invents an accreditation, rating or superlative, uses a forbidden phrasing, or changes the price basis | `no_source` / `forbidden_phrase` / `conflict_or_expired` |

The two negative controls (`01`, `02`) each list at least six true sentences under `must_not_flag`.
In `02` the disclosures are deliberately **not** written with the stored wording: "excl. VAT" becomes
"plus VAT" or "before VAT", "minimum 12-week stay" becomes "as long as you stay with us for at least
12 weeks", "12-month minimum term" becomes "a year at a time", "coursebook not included" becomes
"you buy the coursebook separately", and so on. A checker that only does substring matching on the
disclosure will raise false warnings there. That is the point of the control.

The drafts imitate the free ChatGPT, Claude and Gemini chats: a preamble, a closing offer, and many
heading styles: `=== 1 TIKTOK ===` markers, `**1. Facebook**`, `## 1. Newsletter`, `### Option 1 — TikTok`,
emoji headings (`🎬 TikTok`, `📣 Facebook post`), `Here's your LinkedIn post:`, `TIKTOK:`, `1 NEWSLETTER`,
`1) Facebook`, `Tweet —`, `---` separators with no heading, and one piece with no heading at all
(`pellow-driving-school/04`). Slots appear as `[[key]]`, `\[\[key\]\]`, `{{key}}`, or with the value
typed out. The slot name is always the fact key. Trap sentences always type the value out, so the
expected label is the evidence label and not `slot_blocked`. `contains` never includes a slot token
or a `Subject:` prefix.

## Scoring (contracts §5)

Same as v1: an expected finding is **hit** if some reported finding has the same `label` (and the same
`fact_key`, when one is given) and its sentence contains `contains`. The `must_not_flag` sentences must
get no blocking finding. A blocking finding on any other sentence counts as a false warning. `blocked`
must match. Plan gate: 0 misses on the traps, at most 1 non-blocking `review` per clean draft, and
0 blocking findings on `01-clean` and `02-paraphrase`.

Each expected file begins with a one-line `# WHY:` comment that explains the label.

## Traps per company

| Company (type) | Task scope · channels · publish | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `pellow-driving-school` (driving school, manual + automatic) | variants=manual · tiktok, instagram, google_business · 10-12 | £60 off 20-hour intensive (ends 10-05) | free first lesson (summer) | automatic lessons £42 per hour | 58% first-time pass rate without "July 2025 to June 2026"; £350 block without "paid in advance" | "Pass guaranteed", "DVSA-approved driving school" (forbidden: DVSA approves instructors, not schools); "RoSPA-accredited", "Castlebrook's highest pass rate" (no_source) | instructor franchise fee · Ferrybridge College rate |
| `keepwell-storage` (self storage, 3 sites) | sites=Millgate · facebook, whatsapp, flyer · 10-14 | free van hire on move-in day (ends 10-10) | £15 student summer rate | Ashby Road £21 per week for a 50 sq ft unit (Millgate is £24) | 50% off first 8 weeks without "minimum 12-week stay" | "100% secure" (forbidden); "4.9 stars by over 2,000 customers", "cheapest self storage in town" (no_source) | manager discount limit · Halden Removals trade rate |
| `thornbury-vets` (vet practice, 2-tier health plan) | plan_tiers=essential, segments=dogs · newsletter, sms, instagram · 10-15 | free nurse dental check (ends 10-09) | £10 spring microchipping | 10% off other treatment (Complete tier only) | £16.50 a month without "12-month minimum term" | "Veterinary Hospital" (forbidden: General Practice level), "open 24 hours" (forbidden: emergencies go to Vetline); "award-winning", "Voted the best vets" (no_source) | plan sign-up target · Wendham Kennels contract |
| `inkwright-print` (B2B commercial printer) | segments=business, regions=uk · linkedin, email, website · 10-07 | 15% off roller banners, code AUTUMN15 (ends 10-04) | free paper sample pack | trade (reseller) price £19 for 500 cards (business accounts pay £29) | next-day dispatch without its condition "artwork approved by 12 noon" | "carbon neutral" (forbidden on the FSC fact); "Trusted by over 10,000 UK businesses", "the UK's fastest printer" (no_source) | business-card margin target · Brightwell Estates contract |
| `linden-language-school` (adult language school) | variants=evening-group, segments=adults · x, youtube, blog · 10-08 | £30 early-bird discount (ends 10-02) | £290 summer intensive | one-to-one lessons £45 per hour | 91% level result without "2025-26 end-of-course tests"; £240 without "coursebook not included" | "£240 for you and a friend" (conflict: price is per person); "all native speakers" (forbidden); "British Council accredited" (no_source) | teacher hourly pay · Harrowgate Logistics corporate rate |

Trap kinds covered: another site's price, a plan-tier-only feature, another variant's price, a trade
segment's price, offers that expire between today and publish, offers already expired, a missing
condition (the noon artwork cut-off), missing term/basis disclosures, a per-person price restated as a
price for two, invented accreditations and ratings, and forbidden regulated wording ("Veterinary
Hospital", "DVSA-approved driving school", "carbon neutral", "100% secure").

Values from internal and restricted facts never appear in any draft. A pack or a checker that
places them in the pack or in output text is a leak.

## Validate

```bash
cd 23-eval-suite
python cases/companies-v6/validate_fixtures.py cases/companies-v10     # stdlib + PyYAML
```

The v6 copy of the validator also requires at least six `must_not_flag` sentences on `01-clean` and
`02-paraphrase`. It checks the Fact v2 shape and enums, that every draft has an expected file, that every
`contains` and `must_not_flag` substring occurs in its draft, that every `fact_key` exists, and that
the traps are real: the expired fact is invalid on `publish_on`, the wrong-scope fact is outside the task
scope under the §1 rule, the disclosure is really missing, clean-draft slots are valid, in scope and
public, and no internal or restricted value appears in a draft.
