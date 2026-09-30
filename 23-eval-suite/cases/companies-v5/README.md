# Company fixtures v5 (held out) — Phase 1 task bridge

Five more **fictional** businesses, in industries the earlier sets do not use. Each has scoped facts, one
task and six pasted chatbot answers, with the labels a careful checker should give. The format is the same
as `../companies/` (contracts `_dev/phase1-contracts.md` §1, §4, §5). They were written from the contracts
and the v1 format only, without looking at any checker code, results, or the v2–v4 sets.

**Everything here is invented.** The companies, people, customers, prices, certificate and register numbers,
URLs (`*.example`) and contract terms do not exist. Real scheme names (NSI, BS EN 50131, FHRS, WiredScore,
IMI, Cambridge B2 First) are used only as context. The fictional companies' grades and numbers under
those schemes are made up. LSQB is a made-up accreditation body.

**Held out.** Whoever builds or tunes `88-task-bridge`, the 05 fact store or the claim checker must not
read these files or tune code to them. If a checker misses a case, fix the checker in general. Do not add a
rule for one sentence, and do not edit an expected file so that it passes.

"Today" is **2026-09-29** for every company. Every task publishes in October 2026, after an offer that is
valid today has ended.

## What each company covers

Each company has 10 facts. Every company has all of these:
- an **internal** fact and a **restricted** fact. Their values never appear in any draft.
- one **already expired** fact (`status: expired`).
- one fact that is **valid today but expired at `publish_on`**. `03-expired` also describes this offer
  once in words only, with no number, for example "for a tenner", "a tenth off" or "at no charge".
- a **scope trap**: a valid public fact whose scope is outside the task scope.
- a **certification or credential** with a specific identifier and a grade or tier.
- a **non-numeric inclusion**.
- a **segment-limited or plan-limited price**.

The drafts use varied headings: `=== 1 GOOGLE ===`, `## 1) Google Search Ad`, `**1. Instagram**`, `— Email —`,
`Instagram 👇`, `→ LinkedIn`, `Part 1 (Website copy)`, `1️⃣ Google ad`, `LinkedIn post ↓`, and bare `LINKEDIN`.
**Eight drafts have no heading on the last piece.** In those, the last piece starts at `Subject:`, at a blog
title or `Title:`, or it is an SMS that follows the email sign-off or the website text:
`ironbridge/02`, `greenleaf/01`, `greenleaf/05`, `atlas/02`, `atlas/05`, `nimbus/04`, `redline/03` and `redline/06`.

The negative controls (`01-clean`, `02-paraphrase`) contain harmless look-alikes that must not be flagged:
- ironbridge: "family silver" and "Grade II listed", which look like NSI Silver and Grade 2.
- greenleaf: "No Greenleaf Card needed for that price" (true for the 3-litre price), and "gold"/"golden".
- atlas: "No grades, no pressure" and "B1 or B2?".
- nimbus: "Leeds residents", which looks like the Resident plan, and "on 24/7", which looks like 24/7 access.
- redline: "Free up your day" and "a fleet of family cars".

## Traps per company

| Company (type) | Task scope · publish · pieces | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `ironbridge-security` (alarm & CCTV installer) | regions=north-west, segments=residential · 10-08 · google, email | free video doorbell (ends 10-05) | NSI Silver (replaced by Gold 02-28) | 20-min keyholder response (Yorkshire only; NW is 30) | Essential £24.99/month without "12-month minimum term" | NSI-7042198 vs NSI-7042189, Grade 3 vs Grade 2 (conflict); "most trusted", 4.9/5, police-vetted C&G "specialist" (no_source) | 2027 price rise · Castlegate contract |
| `greenleaf-garden-centre` (garden centre + café) | segments=everyone · 10-15 · instagram, email, sms | 3 bulb bags for £10 (ends 10-11) | 20% off garden furniture | £49.99 10-litre acer (Greenleaf Card holders only) | free delivery over £40 without "within 10 miles of the centre" | "100% peat-free garden centre" (forbidden: only compost is peat-free); 5-star hygiene vs 4 (Good), FHRS ID 1487320 vs 1487302 (conflict); biggest, 4.8 stars, favourite, RHS-qualified staff (no_source) | grotto date · Oakfield trade discount |
| `atlas-language-school` (language school) | variants=online, segments=adults · 10-06 · website, blog | 10% early-bird (ends 10-03) | £450 A2 Summer Intensive | £1,650 B2 (Bristol campus only) | 92% B2 First pass rate without "48 candidates, 2025-26" | LSQB-2471 vs LSQB-2417, grade 1 (Outstanding) vs 2 (Good) (conflict); top-rated, Trustpilot 4.9/5, DELTA examiner teachers (no_source) | 2027 fee rise · Hartwell corporate rate |
| `nimbus-coworking` (coworking, two sites) | sites=wharf, plan_tiers=flex, segments=freelancers · 10-12 · linkedin, email | half-price first month (ends 10-09) | £10 summer day pass | 24/7 access (Resident only); £249 Headrow price; £149 charity price | £199/month without "plus VAT" | WiredScore Platinum vs Gold, WS-UK-40921 vs WS-UK-40912 (conflict); best-connected, 4.9 stars, Cisco-certified IT team (no_source) | Headrow refurb · Crestline office price |
| `redline-auto` (car servicing garage) | segments=private · 10-13 · google, website, sms | free winter check (ends 10-07) | £39 air-con regas | £35 MOT (fleet accounts only; private is £45) | warranty-safe servicing without "using OE-quality parts" | "main dealer standard" (forbidden); every technician a Master Technician (only Dan; others Level 3), IMI ID 0214898 vs 0214889 (conflict); best-rated, 4.9★ (no_source) | 2027 MOT rise · Brightway fleet contract |

Labelling conventions follow `../companies/README.md`:
- A look-alike identifier, or a grade or tier that differs from the fact, is `conflict_or_expired` with the fact's key.
- A true fact used for another site, plan, segment or variant is `wrong_scope`, even when an in-scope fact
  with a different value exists (Yorkshire 20 min vs North West 30 min, fleet £35 vs private £45).
- Superlatives, ratings and staff credentials with no fact behind them are `no_source` with `fact_key: null`.
- Subject lines, ad headlines and SMS lines that carry a trap are listed as their own findings.

## Validate

```bash
cd ../companies && python validate_fixtures.py ../companies-v5     # stdlib + PyYAML
```

On 2026-09-29 it reported: `5 companies, 50 facts, 30 drafts, 0 problems`. Every `contains` and `must_not_flag`
substring was also checked to sit inside one sentence (split on line breaks and on `.`/`!`/`?`). Every
line a finding does not name was checked by hand: it is either true, in scope and valid on `publish_on`,
or it carries no fact value (for example "The evenings are drawing in." or "Autumn colour, supersized").
Each SMS is 160 characters or fewer.
