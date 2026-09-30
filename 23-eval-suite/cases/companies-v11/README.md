# Company fixtures v11 (held out) — Phase 1 task bridge

Five **fictional** businesses in industries not used by earlier sets, each with ten scoped facts, one
task with three pieces and six pasted chatbot answers with the labels a careful checker should give.
They test the Phase 1 path (facts in 05 → task pack from 88 → pasted answer → evidence labels) with
**zero model calls**.

**Everything here is invented.** The companies, people, towns (Wrenford, Ashcombe), clients, prices,
certificate references, URLs (`*.example`) and contract terms do not exist. Any resemblance to a real
business is accidental.

**Held out.** This set was written from the contracts only (`_dev/phase1-contracts.md` §1, §4, §5)
and the format of the v1 set (`../companies/`), without reading any checker code, any results, or the
sets v2 to v10. Whoever builds `88-task-bridge`, `44-claim-checker` or the 05 fact store must not read
these files or tune code to them. If a checker misses a case, fix the checker in general. Do not add a
special rule for one sentence here, and do not edit an expected file so that it passes.

"Today" for every company is **2026-09-29**. Every task publishes in October 2026, after one fact that
is valid today has expired. Each company also has one fact that had already expired.

## Layout

```
<slug>/facts.yaml        {company: {name, type, today}, facts: [Fact v2 x 10]}      (contracts §1)
<slug>/task.yaml         {goal, pieces, scope, publish_on, audience, notes}        (POST /tasks, §4)
<slug>/drafts/NN-name.txt             the pasted chatbot answer
<slug>/drafts/NN-name.expected.yaml   {blocked, findings[{contains,label,fact_key}], must_not_flag}  (§5)
```

Each company has exactly 10 facts: one `internal` and one `restricted` (their values never appear in
any draft), scoped facts on the task's dimension and on a sibling value (the scope trap), one fact
valid today that ends before `publish_on`, one already `expired` fact, conditions, required
disclosures, and `forbidden_phrasing` / `claim_class` where they are natural.

The six drafts are the same for every company:

| Draft | What it does | Expected |
|---|---|---|
| `01-clean` | correct, in scope, all disclosures present | `blocked: false`, no blocking finding |
| `02-paraphrase` | the same facts in everyday words, disclosures reworded too, no new numbers (negative control) | `blocked: false` |
| `03-expired` | uses a fact that is expired on `publish_on` (and one already expired) | `conflict_or_expired` |
| `04-wrong-scope` | uses a true fact from another site, plan, variant or segment | `wrong_scope` |
| `05-missing-disclosure` | uses a fact but leaves out its required disclosure (in two companies, the disclosure is a condition: a minimum visit length, a headcount limit) | `missing_disclosure` |
| `06-invented` | invents an award, rating, ranking or certificate, uses a forbidden phrasing of a true fact, or confuses the price basis | `no_source` / `forbidden_phrase` / `conflict_or_expired` |

The drafts are written the way the free ChatGPT, Claude and Gemini chats answer, and the heading
styles vary much more than in v1: the pack's `=== 1 LINKEDIN ===` markers, `**Instagram caption**`,
`### 1. LinkedIn post`, emoji headings (`📍 Google Business post`, `🎬 TikTok`), `Option 1 – Google
Business Profile`, `1)`, `➊`, `Instagram —`, `LINKEDIN 👇`, `1 INSTAGRAM` with `-----` separators,
underlined `LinkedIn` / `--------`, `(Threads)`, "Here's your Instagram caption:" / "And the WhatsApp
reply:", and one piece with no heading at all after a `———` separator (`spannerworks-garage/05`). Most
drafts have a preamble and a closing offer; some start straight in. Fact slots appear as `[[key]]`,
`\[\[key\]\]`, `{{key}}`, or the value typed out (sometimes in words: "forty-five pounds", "one
thousand eight hundred and ninety-nine pounds"). The slot name is always the fact key. Trap sentences
always type the value out, so the expected label is the evidence label and not `slot_blocked`.

## Scoring (contracts §5)

Same as v1 (`../companies/README.md`):

- An expected finding is **hit** if some reported finding has the same `label` (and the same
  `fact_key`, when one is given) and its sentence contains the `contains` substring. `contains` never
  includes a slot token.
- The sentences in `must_not_flag` must get **no blocking finding**. Clean and paraphrase drafts list
  at least six of them.
- A blocking finding on any other sentence that the expected file does not list counts as a **false
  warning**.
- The **`blocked`** value must match.
- Blocking labels: `conflict_or_expired`, `wrong_scope`, `missing_disclosure`, `forbidden_phrase`,
  `slot_blocked`, `no_source` (only for claim-class keywords). `match` and `review` never block.

Each expected file begins with a one-line `# WHY:` comment that explains the label.

## Companies and channels

| Company | Industry | Kind | Task scope · publish | Channels |
|---|---|---|---|---|
| `spannerworks-garage` | car garage and MOT centre | B2C, **multi-site** (Canal Street + Ashby Road) | sites=canal-street · 10-06 | google_business, sms, facebook |
| `wobblegong-parties` | children's party entertainer | B2C sole trader, packages | variants=magic-show · 10-09 | instagram, whatsapp, flyer |
| `brightfold-cleaning` | office and home cleaning | **B2B** + domestic segments | segments=business · 10-07 | linkedin, email, blog |
| `payroo` | HR and payroll SaaS | **B2B, plan tiers** (Essentials / Growth / Scale) | plan_tiers=growth · 10-13 | linkedin, newsletter, x |
| `hillclimb-ebikes` | e-bike shop | B2C, product variants | variants=trail-e1 · 10-08 | tiktok, youtube, threads |

## Traps per company

| Company | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden / basis (06) | Internal · restricted |
|---|---|---|---|---|---|---|
| `spannerworks-garage` | free winter health check with every MOT (ends 10-03) | £39 air-con re-gas | £139 full service is Ashby Road's price (Canal Street is £149) | £45 MOT without "free partial retest within 10 working days" | "highest-rated MOT centre", "Award-winning" (no_source); "DVSA approved garage" (forbidden; DVSA-authorised test centre only); "lifetime warranty" vs 12 months (conflict) | fleet discount · Harwood Taxis contract |
| `wobblegong-parties` | free Halloween balloon add-on (booked by 10-05) | £150 Summer Garden special | £260 Disco Party in a Magic Show task | £180 without "up to 30 children" (headcount condition) | "Voted No.1", "5 stars from 500+ parents" (no_source); "police approved" (forbidden; enhanced DBS only); "£180 per child" (basis: flat per party) | school-fair floor · Parkside PTA rate |
| `brightfold-cleaning` | free first deep clean if signed by 10-02 | 10% off first three home cleans | £18/hour home cleaning (domestic only) in a business task | £21/hour without "plus VAT" and "minimum 2 hours per visit" (missing condition) | "London's most trusted", "ISO 9001 certified" (no_source); "chemical-free" (forbidden; EU Ecolabel products only) | margin floor · Quarry Lane contract |
| `payroo` | free payroll migration if switched by 10-09 | first 3 months free | open API (Scale only); £4 Essentials price in a Growth task | £7 without "excl. VAT" and "minimum 5 employees" | "Trusted by 10,000+", "UK's fastest" (no_source); "HMRC approved" (forbidden; HMRC-recognised only); "£7 a month for your whole team" (basis: per employee) | Growth price floor · Brackenridge contract |
| `hillclimb-ebikes` | demo day on 10-03 | £200 off the Trail E1 | £1,499 City C2 price in a Trail E1 task | 70-mile range without "in eco mode"; 0% APR without "representative 0% APR" / "subject to status" | "UK's best-selling" (no_source); "100% waterproof" (forbidden; IPX4 only); "100 miles" vs 70 (conflict) | Trail E1 floor price · council scheme price |

Truthful facts whose phrasing is forbidden: DVSA authorisation ("DVSA approved garage", "government
approved"), an enhanced DBS check ("police approved"), EU Ecolabel products ("chemical-free",
"non-toxic"), HMRC recognition ("HMRC approved", "HMRC endorsed"), IPX4 ("waterproof"). The clean and
paraphrase drafts state each of these facts in allowed words, and those lines are in `must_not_flag`.

Basis traps: a flat per-party price stated per child, a per-employee price stated per team. Missing
condition traps: a minimum visit length and a headcount limit carried as required disclosures.

Values from internal and restricted facts never appear in any draft. A pack or a checker that places
them in the pack or in output text is a leak.

## Validate

```bash
cd 23-eval-suite
python cases/companies-v6/validate_fixtures.py cases/companies-v11     # stdlib + PyYAML
```

Result when written (2026-09-30): `5 companies, 50 facts, 30 drafts, 0 problems`.
