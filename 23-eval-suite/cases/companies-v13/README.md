# Company fixtures v13 (held out) — Phase 1 task bridge

Five **fictional** businesses in industries that no earlier set uses: a florist with two shops, a B2B
CRM SaaS with plan tiers, a children's swim school, a B2B bike courier and a caravan/holiday park. Each
has 10 scoped facts, one task with three pieces and six pasted chatbot answers with the labels a careful
checker should give. They test the Phase 1 path (facts in 05 → task pack from 88 → pasted answer →
evidence labels) with **zero model calls**.

**Everything here is invented.** The companies, people, customers, prices, certificates, certifying
bodies ("AquaTeach", "National Swim Inspectorate", "Golden Stem Award"), lenders, URLs (`*.example`)
and contract terms do not exist. Any resemblance to a real business is accidental.

**Held out.** Whoever builds `88-task-bridge`, `44-claim-checker` or the 05 fact store must not read
these files or tune code to them. They were written from the contracts only
(`_dev/phase1-contracts.md` §1, §4, §5) and the v1 format in `../companies/`, without reading any
checker code, checker results or the sets v2 to v12. If a checker misses a case, fix the checker in
general. Do not add a special rule for one sentence here, and do not edit an expected file so that it
passes.

"Today" for every company is **2026-09-29**. Every task publishes in October 2026, after one fact that
is valid today has expired.

## Layout

```
<slug>/facts.yaml        {company: {name, type, today}, facts: [Fact v2 ...]}     (contracts §1)
<slug>/task.yaml         {goal, pieces, scope, publish_on, audience, notes}        (POST /tasks, §4)
<slug>/drafts/NN-name.txt             the pasted chatbot answer
<slug>/drafts/NN-name.expected.yaml   {blocked, findings[{contains,label,fact_key}], must_not_flag}  (§5)
```

Every `facts.yaml` has exactly 10 facts: one `internal`, one `restricted`, one active fact that expires
between today and `publish_on`, one fact that had already expired, at least one fact scoped to a site,
region, segment, plan tier or variant outside the task, conditions, required disclosures, and a
forbidden phrasing where one is natural.

The six drafts are the same for every company:

| Draft | What it does | Expected |
|---|---|---|
| `01-clean` | correct, in scope, all disclosures present, some values as slots | `blocked: false`, no blocking finding |
| `02-paraphrase` | the same facts, freely reworded, no new numbers (negative control) | `blocked: false` |
| `03-expired` | uses a fact that is expired on `publish_on` and one already expired | `conflict_or_expired` |
| `04-wrong-scope` | uses a true fact from another site, plan, variant, region or segment | `wrong_scope` |
| `05-missing-disclosure` | uses a fact but leaves out its required disclosure | `missing_disclosure` |
| `06-invented` | invents an award, rating or number, breaks a fact's conditions, or uses a forbidden phrasing | `no_source` / `conflict_or_expired` / `forbidden_phrase` |

## What is harder than v1

- **Loose wording.** About half the traps are stated plainly with typed values ("Peony bunches are 25%
  off", "Zone 2 drops are £11 excl. VAT"). The other half are loose: "a fifth off", "Forty percent
  off", "your first week's deliveries are on us", "Forty-five quid a head", "twenty-nine quid a seat
  each month", "two hundred and forty-nine quid", "Nine pound fifty a go", "any day of the week",
  "any time, day or night", "every plan comes with single sign-on".
- **Truthful paraphrases.** The `02` drafts use number words ("Thirty-five pounds", "Seven pounds
  fifty"), abbreviations ("+VAT", "£29/user/mo", "max. 6 per teacher", "18+, T&Cs apply", "9am-7pm",
  "till 3 Jan"), everyday wording for disclosures ("plus VAT, paid yearly", "paid termly", "Delivery
  is £6.50 within 5 miles", "subject to status"), rearranged sentences and true loose phrases ("for the
  pair of you", "any day of the week" for a pool that is open every day). They must not be flagged.
- **Condition breaks.** Some `06` traps use a real fact outside its conditions: same-day delivery "any
  day of the week" (Monday to Saturday only), 60-minute delivery "any time, day or night" (weekdays 8am
  to 6pm), group swimming "from age 3" (ages 4 to 11).
- **Varied answer shapes.** `=== 1 INSTAGRAM ===` markers, `**1. Facebook**`, `1)`, `### LinkedIn post`,
  `TikTok —`, `LinkedIn — <text on the same line>`, emoji headings (`🌸 Instagram`, `▶️ YouTube`),
  underlined headings (`Facebook post` / `----`), `1 FACEBOOK`, one piece with no heading at all, `---`
  separators, preambles and sign-offs. Slots come as `[[key]]`, `{{key}}`, `\[\[key\]\]`, or the value
  typed out. Trap sentences always type the value out, so the expected label is the evidence label
  and not `slot_blocked`.

## Scoring (contracts §5)

- An expected finding is **hit** if some reported finding has the same `label` (and the same `fact_key`,
  when one is given) and its sentence contains the `contains` substring. `contains` never includes
  a slot token, so it matches both the raw pasted text and the text after slots are filled.
- The sentences in `must_not_flag` must get **no blocking finding**.
- A blocking finding on any other sentence that the expected file does not list counts as a **false
  warning**. An extra finding on a sentence that is already expected does not count.
- The **`blocked`** value must match (it is true when any finding has a blocking label).
- Blocking labels: `conflict_or_expired`, `wrong_scope`, `missing_disclosure`, `forbidden_phrase`,
  `slot_blocked`, `no_source` (the last one only for claim-class keywords). `match` and `review` never
  block, so an extra `review` on a clean draft is allowed.
- Plan gate: 0 misses on the traps, at most 1 non-blocking `review` per clean draft, and 0 blocking
  findings on `01-clean` and `02-paraphrase`.

Each expected file begins with a one-line `# WHY:` comment that explains the label.

## Traps per company

| Company (type) | Task scope · channels · publish | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden / condition (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `larkspur-stem` (florist, 2 shops) | sites=Market Square · instagram, pinterest, google_business · 10-12 | half price first subscription month (ends 10-04), "get half price on your first month" | peony sale 25% (ended 07-31) | £45 wreath workshop (Harbour Street only), plain and "Forty-five quid a head" | same-day delivery without "£6.50 delivery fee within 5 miles", plain and "Order by one o'clock … today" | "Golden Stem Award" (no_source); "100% pet-safe" (forbidden); same-day "any day of the week" (conflict, Mon–Sat only) | rose cost per stem · Grandholt Hotel weekly contract |
| `pivotdesk-crm` (B2B CRM SaaS, plan tiers) | plan_tiers=team · linkedin, email, x · 10-14 | free spreadsheet migration (ends 10-06) | 40% launch discount (ended 06-30), "Forty percent off" | £9 Solo price; "every plan comes with single sign-on" (Scale only) | £29 Team price without "excl. VAT, billed annually", plain and "twenty-nine quid a seat" | "5,000+ sales teams", "4.8 out of 5" (no_source); "unhackable" (forbidden) | monthly churn · Harrowgate Logistics seat price |
| `splashlings-swim` (children's swim school) | variants=group · facebook, whatsapp, flyer · 10-19 | free taster lesson (ends 10-11) | £60 summer intensive (ended 08-28) | £28 private lesson, plain and "Twenty-eight pounds … all to themselves" | £9.50 group lesson without "billed per term", plain and "Nine pound fifty a go" | "National Swim Inspectorate" rating (no_source); "drown-proof" (forbidden); "from age 3" (conflict, ages 4–11) | teacher hourly pay · St Aldric's Primary per-pupil price |
| `copperline-couriers` (B2B bike courier) | regions=zone-1, segments=account · linkedin, newsletter, threads · 10-07 | 15% off first month for new accounts (ends 10-03) | free first week (ended 07-31), "your first week's deliveries are on us" | £11 Zone 2 rate, plain and "Eleven quid a drop, plus VAT" | £7.50 without "excl. VAT" (plain and "Seven pound fifty"); 60 minutes without "weekdays 8am to 6pm" | "fastest courier in the city" (no_source); "fully insured" (forbidden, cover capped at £1,000); 60 minutes "any time, day or night" (conflict) | rider pay per drop · Bramwell & Co drop rate |
| `saltmarsh-sands` (caravan & holiday park) | variants=touring · tiktok, sms, youtube · 10-16 | 20% touring half-term offer (ends 10-09), "A fifth off" | 10% early bird (ended 01-31) | £249 static caravan 3-night break, plain and "two hundred and forty-nine quid" | dogs without "£3 per dog per night"; lodge finance without "Finance subject to status. 18+. T&Cs apply" | "UK's best holiday park" (no_source); "twenty quid a night" (conflict, £32); "guaranteed finance", "no credit checks" (forbidden) | October occupancy target · Ridgeway Caravan Rally pitch rate |

Regulated claims covered: a pet-safety claim (`regulated_health`, lily-free range, "100% pet-safe" and
"non-toxic" forbidden); an information-security certificate (`security`, ISO 27001, "unhackable" and
"SOC 2" forbidden); children's swimming safety (`safety_cert`: teacher certificate, poolside lifeguard,
1:6 ratio, "drown-proof" forbidden); capped goods-in-transit insurance ("fully insured" forbidden); and
consumer finance on lodge sales (18+, subject to status, "guaranteed finance" and "0% APR" forbidden).

Values from internal and restricted facts never appear in any draft. A pack or a checker that
places them in the pack or in output text is a leak.

## Validate

```bash
cd 23-eval-suite
python cases/companies-v6/validate_fixtures.py cases/companies-v13     # stdlib + PyYAML
```

Result when written (2026-09-30): `5 companies, 50 facts, 30 drafts, 0 problems`.
