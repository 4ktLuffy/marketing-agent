# Company fixtures v9 (held out): five new industries

Five more **fictional** businesses in industries the earlier sets do not cover: an optician, a wedding
venue, an EV charger installer, a dog groomer and an IT training provider. Each has 10 scoped facts,
one task with three pieces and six pasted chatbot answers, with the labels a careful checker should give.
The layout, the contracts (`_dev/phase1-contracts.md` §1, §4, §5) and the scoring rules are the same as
in `../companies/README.md`.

**Everything here is invented.** The companies, people, registers (GOC numbers, "NESR", "CGQB",
"CloudCert Institute", FHRS references), licences, contracts and URLs (`*.example`) do not exist. Any
resemblance to a real business or record is accidental.

**Held out.** Written from the contracts and the v1 format only, without reading any checker code
(`88-task-bridge/`, `05-brand-service/app/`, `44-claim-checker/`), any results, or the other v2–v8 sets.
If a checker misses a case, fix the checker in general. Do not add a rule for one sentence here and do
not edit an expected file so that it passes.

"Today" is **2026-09-29** for every company. Every task publishes in October 2026, after one fact that is
valid today has expired.

## Layout

```
<slug>/facts.yaml        {company: {name, type, today}, facts: [Fact v2 x 10]}      (contracts §1)
<slug>/task.yaml         {goal, pieces (3), scope, publish_on, audience, notes}      (POST /tasks, §4)
<slug>/drafts/NN-name.txt             the pasted chatbot answer
<slug>/drafts/NN-name.expected.yaml   {blocked, findings[{contains,label,fact_key}], must_not_flag}  (§5)
```

Each company's 10 facts include: an **internal** and a **restricted** fact (their values never appear in
any draft); one fact **valid today but expired by `publish_on`**; a **scope trap** (a true public fact,
valid at publish, outside the task scope under the §1 rule); a **credential with an identifier and a
grade**; a **non-numeric inclusion**; and a **segment-limited price**.

| Draft | What it does | Expected |
|---|---|---|
| `01-clean` | correct, in scope, disclosures present verbatim, slots in `[[key]]` style | `blocked: false`, ≥8 `must_not_flag` |
| `02-paraphrase` | same facts reworded, values typed out, disclosures in **everyday equivalent wording** ("pp", "a head", "each", "VAT extra", "+ VAT", "within a month", "frames extra", "pups younger than six months") | `blocked: false`, ≥8 `must_not_flag` |
| `03-expired` | the expiring offer **by name and loosely described** (heading, subject and body) | `conflict_or_expired` on every such line |
| `04-wrong-scope` | a true fact from another site, variant or segment | `wrong_scope` on every such line |
| `05-missing-disclosure` | a fact whose disclosure is absent **in any wording**, in the subject/headline and in the body | `missing_disclosure` |
| `06-invented` | look-alike identifier with unusual separators, a different grade in words (both `conflict_or_expired` against the credential), a superlative with a place, a rating, and a staff/service claim (`no_source`) | blocking |

The negative controls are deliberately tempting: questions and short labels that name products
("Clearview varifocals? £149 a pair, frames extra."), idioms ("Harrogate's best-kept secret?",
"Top tip: …"), "free" in a true inclusion ("free adjustments", "one free resit"), and numbers of guests
that belong to a different fact. Several last pieces have no heading (the chatbot just starts the next
piece after a blank line or `---`), and headings vary: `=== 1 INSTAGRAM ===`, `**📸 Instagram caption**`,
`### 1. Instagram`, `SMS:`, `1 SMS`, `**WhatsApp message:**`, `Page title:`.

`contains` strings are verbatim and always sit inside one sentence (split on `.`/`!`/`?` + space and on
newlines); they never include a slot token. Every line that uses a trap is listed, including headings
and subject lines. Every remaining line was hand-checked: it is a greeting, heading, emoji, hashtag,
call to action or sign-off, or it is listed in `must_not_flag`.

## Traps per company

| Company (type) | Task scope · publish | Expired at publish (valid today) | Scope trap (04) | Missing disclosure (05) | Invented (06) | Credential (id · grade) | Inclusion · segment price | Internal · restricted |
|---|---|---|---|---|---|---|---|---|
| `aurora-opticians` (optician, Harrogate + Ripon) | sites=harrogate, segments=private · 10-08 | Autumn Two-for-One (ends 10-04); also Summer Shades (ended 08-31) | free NHS sight test (segment nhs-eligible) | Clearview varifocals £149 without "frames not included" | "best opticians in Harrogate", 4.9 stars, free home visits, same-day glasses; GOC "01/34871", "Higher Certificate" | GOC 01-34817 · Professional Certificate in Glaucoma | aftercare + hard case · NHS test free for eligible only | 2.6x frame markup · Wharfedale Care contract |
| `cedar-wedding-venue` (wedding venue) | variants=winter-weekday · 10-12 | Autumn Booking Bonus (ends 10-10) | Tithe Barn seats 180 (summer packages only) | Winter Weekday £6,950 without "based on 60 day guests" | "most romantic venue in Yorkshire", 5 stars, dedicated coordinator, on-site florist; "FHRS 11.88.24", "Four (Good)" | FHRS 118842 · rating 5 (Very Good) | mulled-wine reception + bridal suite · planner commission (planners, internal), Pennine Bank rate (corporate, restricted) | 12% planner commission · Pennine Bank £48/head |
| `volt-ev-charging` (EV charger installer) | regions=west-yorkshire, segments=homeowners, variants=single-phase · 10-09 | Home Chargepoint Grant (closes 10-05) | Volt Pro 22 £1,449 (three-phase); Volt Work Twin £2,150 + VAT (business) | Volt Home 7 £899 fitted without "standard installation" | "number one in West Yorkshire", 4.9/5, employed engineers + same-day fitting, 24-hour survey; "NESR:207.713", "Master Contractors" | NESR-207731 · Approved Contractor | app set-up, load-balancing check, EIC · Work Twin (business) | installer day cost · Hollins Housing contract |
| `bramble-dog-grooming` (dog groomer, Otley + Ilkley) | sites=otley · 10-10 | Muddy Paws Month (ends 10-06) | £42 small-dog groom (Ilkley price; Otley is £38) | Puppy Pamper £45 without "for puppies under 6 months" | "best groomer in Wharfedale", 5 stars, vet-nurse groomers, free pick-up; "CGQB/3/55210", "Level Four" | CGQB-3-55120 · Level 3 Diploma (others Level 2) | bath, blow-dry, nail clip, ear clean · Tuesday Club (over-65, unused in drafts) | shampoo cost per wash · Wharfe Kennels contract |
| `summit-it-training` (IT training) | segments=private-sector, variants=online · 10-14 | October Early Bird 15% (ends 10-04) | Leeds classroom £895 + VAT (classroom); £545 + VAT charity/public-sector rate | 94% pass rate without "January to June 2026 cohort of 212 learners" | "number one in the North", 4.9/5, ex-examiner trainers, pass-or-free guarantee; "CCI–TP–40281", "Platinum" | CCI-TP-40218 · Gold Training Partner | exam voucher + one free resit · charity/public-sector rate | trainer day rate · Northgate Council contract |

## Validate

```bash
python ../companies-v6/validate_fixtures.py .      # stdlib + PyYAML; the v6 validator takes a folder path
```

Expected output: `5 companies, 50 facts, 30 drafts, 0 problems`.
