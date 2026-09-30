# Company fixtures v4 (held out) — Phase 1 task bridge

Five **fictional** businesses from industries not used in earlier sets: a dental laboratory, a boat dealer and
servicing yard, an event venue, an accountancy firm and a packaging printer. Each has scoped facts, one task,
and six pasted chatbot answers with the labels a careful checker should give. They test the Phase 1 path (facts
in 05 → task pack from 88 → pasted answer → evidence labels) with **zero model calls**.

**Everything here is invented.** The companies, people, clients, brands (Solent Rider, Tidewater, NordZir,
ProLink, VenueMark, Tallyroo), certificate and dealer numbers, prices and contracts do not exist. Any resemblance
to a real business is accidental. Real schemes named only as context (ISO 13485, ISO 6872, RCD design
categories, UKCA/CE, FSC, ICAEW, HMRC, Making Tax Digital) are used with invented identifiers.

**Held out.** These files were written from the contracts only (`_dev/phase1-contracts.md` §1, §4, §5) and the
v1 fixture format. No checker code, no results, and no v2/v3 sets were read. If a checker misses a case, fix the
checker in general. Do not add a rule for one sentence here, and do not edit an expected file so that it passes.

"Today" is **2026-09-29** for every company. Every task publishes in October 2026, after a fact that is valid
today has expired.

## Layout and scoring

These are the same as v1 (`../companies/README.md`). Six drafts per company: `01-clean` and `02-paraphrase`
are negative controls (`blocked: false`). The other drafts target one trap each: `03-expired`
(conflict_or_expired), `04-wrong-scope` (wrong_scope), `05-missing-disclosure` (missing_disclosure) and
`06-invented` (no_source / conflict_or_expired / forbidden_phrase). Every `contains` substring sits inside a
single sentence and never includes a slot token. Trap sentences type the value out. Every genuine trap line is
listed, including subject lines, bold hooks and blog/website headings.

What is new compared with v1:

- **Piece headings vary**: `=== 1 LINKEDIN ===`, `**1. LinkedIn post**`, `## Email`, `# LinkedIn`, `1) LinkedIn`,
  `**Post 1 – Instagram**`, `Cold email`, `Google Business Profile post`, `Text message:`, `1 INSTAGRAM`, and a
  last piece with no heading at all (06 in meridian, bluewater and northfold).
- **Channels vary**: linkedin, email, google (Business Profile), instagram, sms (≤160 chars), website, blog.
  Northfold has two pieces; the others have three.
- **Slots vary**: `[[key]]` and `{{key}}`, plus values typed out.
- **Harmless look-alikes appear in the negative controls**: "Best wishes", "All the best", "top you up", "Top tip",
  "rated for Design Category C", "your number one job", "Chartered accountants". These must not be flagged.
- **Every 06 has the same five kinds of invention**: a look-alike identifier (two digits swapped), a superlative,
  a rating, a staff credential, and a grade or code change. Kinetic and Summit also add a forbidden phrase.
- **Every 03 also mentions an already-expired offer in passing** (one clause), next to the main
  expires-before-publish offer.

## Traps per company

| Company (type) | Task scope · publish · pieces | Expired at publish (valid today) | Already expired (in passing) | Scope trap (04) | Missing disclosure (05) | Invented (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `meridian-dental-lab` (dental lab, B2B) | variants=crowns-bridges, regions=north-west · 10-08 · linkedin, email, google | £49/unit intro offer on the first three crowns (ends 10-04) | £5 scan-switch rebate (08-31) | "UKCA and CE marked" crowns (marks are on ProLink implant abutments only) | 5 working days without "from receipt of the scan or impression" | "fastest", "4.9/5 by 300 dentists", "GDC-registered master ceramist" (no_source); ISO 6872 **Class 6** vs Class 5, certificate **QMS-44871** vs QMS-44817 (conflict) | unit cost £21 · Pennine Smiles £54/unit |
| `bluewater-marine` (boat dealer + yard) | sites=poole · 10-09 · instagram, email, sms | free winter storage ashore (book by 10-03) | 0% APR over 36 months (09-15) | £395 winter service package (Hamble yard tariff) | 4.9% APR without "subject to status"; 5-year warranty without "when serviced annually by an authorised dealer" | "number one RIB dealer", "5 stars on Google", "Tidewater Master Certified technicians" (no_source); Design Category **B** vs C, dealer code **TW-UK-0139** vs 0193 (conflict) | part-exchange floor · Harbour Patrol £38,400/yr |
| `kinetic-events` (event venue) | segments=corporate · 10-07 · linkedin, website, email | 10% early-booking discount (ends 10-04) | free terrace barbecue lunch (08-31) | £45 day delegate rate (charity only; corporate is £59) | £59 per delegate without "plus VAT" | "Yorkshire's leading", "9.8/10 on VenueScore", "Certified Meeting Professionals" (no_source); VenueMark **Platinum** vs Gold, **VM-G-20471** vs 20417 (conflict); "fully accessible" (forbidden) | rate floor £47 · Vantrell Pharma £86,000/yr |
| `summit-accountants` (accountancy) | segments=sole-trader · 10-12 · google, sms, blog | half price for three months when switching by 10-09 | free bookkeeping catch-up (08-31) | £1,150/yr limited-company accounts + CT return | £45/month without "plus VAT" | "leading firm in North Yorkshire", "4.9 stars from 180 reviews", "Chartered Tax Adviser" (no_source); Tallyroo **Platinum** vs Gold, firm number **C008817246** vs C008812746 (conflict); "registered auditors" (forbidden) | partner hourly floor · Corrie Bakeries £640/month |
| `northfold-print` (printer/packaging) | segments=direct · 10-14 · instagram, email | free gold foil on the lid (order by 10-06) | free sample packs (08-31) | £520 per 1,000 cartons (trade price; direct is £640) | 8 working days without "after artwork approval" | "greenest packaging printer", "4.8 on Trustpilot", "G7 Expert certified colour team" (no_source); **GC2** vs GC1 board, **NFP-COC-008231** vs 008213 (conflict) | press-hour cost · Hartley Foods £0.21/carton |

More subtle points:

- `bluewater-marine/autumn-finance` starts on 2026-10-01. It is **not valid today** but it is valid on
  `publish_on`, so the clean draft may use it. A checker that tests validity on "today" instead of the
  publish date will flag the clean draft wrongly.
- `meridian-dental-lab/iso-13485` is scoped to `[crowns-bridges, dentures]`. The task names only
  crowns-bridges, so it is in scope (task values ⊆ fact values). `ukca-abutments` is out of scope.
- Regulated or credential claims that must pass when true: ISO 13485 with its certificate number, RCD
  Design Category C, VenueMark Gold, the ICAEW firm number, and the FSC chain-of-custody code.

Values from internal and restricted facts never appear in any draft. A pack or a checker that places them in the
pack or in output text is a leak.

## Validate

```bash
python ../companies/validate_fixtures.py .     # stdlib + PyYAML
```

Result when written: `5 companies, 50 facts, 30 drafts, 0 problems`.

Lines that were considered and **not** listed as traps, because they name no value, offer or feature from a
fact: `meridian/05` "That's the Meridian way for crown and bridge cases…"; `bluewater/04` "Winter service season is
here at Bluewater!" (Poole does winter servicing; only the £395 Hamble package is out of scope); `summit/04`
"Fixed fees, no surprises."; `kinetic/04` "Budgets are tight this year…" and the `## Rates` heading.
