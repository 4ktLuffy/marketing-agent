# Company fixtures v3 (held out): five new industries

These are five more **fictional** businesses in industries the v1 set does not cover. Each has scoped
facts, one task and six pasted chatbot answers, plus the labels a careful checker should give. They
test the same Phase 1 path as v1 (facts in 05, task pack from 88, pasted answer, evidence labels)
with **zero model calls**.

**Everything here is invented.** The companies, people, customers, prices, certificate and licence
numbers, schemes (for example "FitMark Safe Gym", "Albion Organic Certification", "Yorkshire Warm
Homes grant") and contract terms do not exist. Real scheme names such as MCS, RCVS, ISO 27001 and
Microsoft Solutions Partner appear only as the kind of credential a real business would hold. The
identifiers attached to them are made up.

**Held out.** These fixtures were written from `_dev/phase1-contracts.md` §1, §4 and §5 and from the
v1 fixture format only. Nobody looked at checker code, at v1 or v2 results, or at `88-task-bridge`
while writing them. Whoever builds or tunes the checker must not read these files. If a case is
missed, fix the checker in general. Do not edit an expected file so that it passes.

"Today" is **2026-09-29** for every company. Every task publishes in October 2026. In every company,
one fact is valid today but has expired by `publish_on`, and one fact had already expired before
today.

## Layout

The layout is the same as v1 (`../companies/README.md`):

```
<slug>/facts.yaml                     {company: {name, type, today}, facts: [Fact v2 ...]}   (§1)
<slug>/task.yaml                      {goal, pieces, scope, publish_on, audience, notes}      (§4)
<slug>/drafts/NN-name.txt             the pasted chatbot answer
<slug>/drafts/NN-name.expected.yaml   {blocked, findings[{contains,label,fact_key}], must_not_flag}  (§5)
```

Each company has 10 facts. They include:

- an internal fact and a restricted fact (their values never appear in any draft);
- an expired fact;
- a fact that is valid today but expired at `publish_on`;
- a scope trap;
- a certification or credential with a specific identifier;
- a non-numeric inclusion or feature;
- a graded identifier (a Level, Tier, EPC band or accreditation level).

The six drafts do the same job as in v1:

- `01-clean` and `02-paraphrase` are negative controls with 0 blocking findings.
- `03-expired`, `04-wrong-scope`, `05-missing-disclosure` and `06-invented` are the traps.

## What is new compared with v1

- **Harder negative controls.** Each `02-paraphrase` contains harmless phrases that a keyword
  checker might wrongly flag: "most of our members/clients/customers/regulars", "free to ask", "the
  best way to", and "qualified leads" (vantage-cloud). They are listed in `must_not_flag`.
- **An offer without its name.** Each `03-expired` describes the offer that is valid today but
  expired at publish once without naming it (for example "skip the £30 joining fee" or "We'll set
  everything up for you at no cost"). The same draft names it elsewhere.
- **A near-miss identifier or grade.** Each `06-invented` changes a real identifier or grade by a
  little: SOL-48231 vs SOL-48213, IS 781240 vs IS 781204, AOC-4471 vs AOC-4417, Level 4 vs Level 3,
  and "Veterinary Hospital" vs "General Practice". It also contains a superlative, a rating, and a
  staff credential that no fact supports.
- **Every trap line is listed.** This includes subject lines, headings and SMS or website one-liners.
  A draft line that the expected file does not name either states no fact (a greeting, a sign-off,
  a question lead-in, or a generic heading such as "Panels built to last") or is in `must_not_flag`.
- **Varied formats.** Headings come as `=== 1 FACEBOOK ===`, `**1. Instagram**`, `## 1. Instagram`,
  `### 1 · Instagram`, `### Post 1 – Instagram`, `1) Facebook`, `Post 1 — Facebook`, `INSTAGRAM`,
  `Instagram caption:`, `📱 SMS:` and `# Email`. Slots come as `[[key]]`, `\[\[key\]\]`, `{{key}}` and
  `【key】` (harvest-table 01), and elsewhere the values are typed out. Trap sentences always type the
  value out.

## Traps per company

| Company (type) | Task scope · publish | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `solace-solar` (solar & battery installer) | regions=yorkshire, variants=aurelis · 10-08 | Warm Homes grant "up to £2,500 off a home battery" (ends 10-04) | Summer Power-Up (13.5 kWh upgrade) | 30-year warranty (Brightwell only; Aurelis is 25) | £9,450 Home 6 without "subject to a home survey" | "number one", "4.9 out of 5" (no_source); certificate SOL-48231 vs SOL-48213 (conflict); "chartered electrical engineers" (no_source) | Home 6 margin · Leeds Community Homes price |
| `pawprint-vets` (vet practice, Ashby + Millford) | sites=Ashby · 10-07 | Golden Years free senior check (ends 10-03) | Summer Flea Amnesty | £42 consultation (Millford; Ashby is £48) | £48 without "medicines and tests charged separately"; NightPaws without "emergency fees apply" | "friendliest", "most trusted", "5 stars on Google" (no_source); "Veterinary Hospital" vs General Practice (conflict); "RCVS Recognised Specialists" (no_source) | medicine markup · Greenacre Kennels price |
| `crestline-fitness` (gym chain, Northgate + Canal Wharf) | sites=Northgate · 10-09 | Autumn Kickstart, no £30 joining fee (ends 10-05) | Summer Pass 3 months for £75 | £39.99 Core (Canal Wharf; Northgate is £34.99) | Core £34.99 without "12-month minimum term"; Off-Peak £24.99 without "access 10am to 4pm on weekdays" | "best gym in the city", "4.8 stars" (no_source); Level 4 vs Level 3 (conflict); "registered physiotherapists" (no_source) | cost per new member · Halden Logistics corporate rate |
| `vantage-cloud` (B2B managed IT) | plan_tiers=essentials, regions=uk · 10-06 | free onboarding worth £1,500 (ends 10-02) | Cloud Move 25% off migrations | 30-minute 24/7 P1 response (Premier only; Essentials is 4-hour) | £29 per user without "excluding VAT, minimum 10 users"; ISO 27001 without "covers our service desk and hosting operations" | "UK's leading", "4.9/5 on Clutch" (no_source); "Microsoft Gold Partner" (forbidden); IS 781240 vs IS 781204 (conflict); "Microsoft Certified Solutions Expert" engineers (no_source) | engineer utilisation target · Brackwater Legal price |
| `harvest-table` (organic granola producer, retail + trade) | channels=retail · 10-12 | Harvest Bundle 3 bags for £12 (ends 10-06) | summer free delivery over £25 | £28.80 per case, 10-case minimum, Tier 2 (trade only) | allergens without "made in a facility that also handles peanuts and sesame"; shelf life without "eat within 4 weeks of opening" | "Britain's best", "5 stars" (no_source); licence AOC-4471 vs AOC-4417 (conflict); "registered nutritionist" (no_source); "Gluten-free" (forbidden) | unit cost · Marlow Fine Foods own-label price |

Graded identifiers by company:

| Company | Graded identifier |
|---|---|
| solace-solar | EPC band D condition on the grant |
| pawprint-vets | RCVS PSS level General Practice (ref PSS-71954) |
| crestline-fitness | trainer Level 3 |
| vantage-cloud | plan tier Essentials vs Premier; ISO 27001:2022 |
| harvest-table | trade price band Tier 2 |

Non-numeric inclusions and features by company:

| Company | Inclusion or feature |
|---|---|
| solace-solar | app monitoring and bird-proofing mesh |
| pawprint-vets | the out-of-hours partner |
| crestline-fitness | spin, HIIT and yoga classes |
| vantage-cloud | the Microsoft designation |
| harvest-table | sweetened only with English honey |

Counts: 5 companies, 50 facts, 30 drafts, 92 expected findings, 77 `must_not_flag` sentences.

## Scoring

Scoring follows the rules in contracts §5. They are unchanged from v1 (see `../companies/README.md`).
Blocking labels are `conflict_or_expired`, `wrong_scope`, `missing_disclosure`, `forbidden_phrase`,
`slot_blocked` and `no_source`. A wrong identifier or grade next to a real fact is labelled
`conflict_or_expired` with that fact's key. An unsupported superlative, rating or staff credential
is labelled `no_source` with `fact_key: null`.

## Validate

```bash
python ../companies/validate_fixtures.py .      # stdlib + PyYAML; 0 problems expected
```
