# Company fixtures (held out) — Phase 1 task bridge

Seven **fictional** businesses of different types, each with scoped facts, one task and six pasted
chatbot answers with the labels a careful checker should give. They test the Phase 1 path
(facts in 05 → task pack from 88 → pasted answer → evidence labels) with **zero model calls**.

**Everything here is invented.** The companies, people, clients, prices, certificates, URLs
(`*.example`) and contract terms do not exist. Any resemblance to a real business is accidental.

**Held out.** Whoever builds `88-task-bridge` or the 05 fact store must not read these files or tune
code to them. They were written from the contracts only (`_dev/phase1-contracts.md` §1, §4, §5),
without looking at any checker code. If a checker misses a case, fix the checker in general. Do not
add a special rule for one sentence here, and do not edit an expected file so that it passes.

"Today" for every company is **2026-09-29**. Every task publishes in October 2026, after at least one
fact that is valid today has expired.

## Layout

```
<slug>/facts.yaml        {company: {name, type, today}, facts: [Fact v2 ...]}     (contracts §1)
<slug>/task.yaml         {goal, pieces, scope, publish_on, audience, notes}        (POST /tasks, §4)
<slug>/drafts/NN-name.txt             the pasted chatbot answer
<slug>/drafts/NN-name.expected.yaml   {blocked, findings[{contains,label,fact_key}], must_not_flag}  (§5)
```

The six drafts are the same for every company:

| Draft | What it does | Expected |
|---|---|---|
| `01-clean` | correct, in scope, all disclosures present | `blocked: false`, no blocking finding |
| `02-paraphrase` | the same facts, freely reworded, no new numbers (negative control) | `blocked: false` |
| `03-expired` | uses a fact that is expired on `publish_on` (and one already expired) | `conflict_or_expired` |
| `04-wrong-scope` | uses a true fact from another site, plan, variant, channel or segment | `wrong_scope` |
| `05-missing-disclosure` | uses a fact but leaves out its required disclosure | `missing_disclosure` |
| `06-invented` | invents a certificate, award, rating or number, or uses a forbidden phrasing | `no_source` / `conflict_or_expired` / `forbidden_phrase` |

The drafts are written the way the free ChatGPT, Claude and Gemini chats answer: a preamble, markdown
bold and headings, some emojis, and a closing offer. Some use the `=== 1 LINKEDIN ===` markers from
the pack. Others use variants such as `**1. LinkedIn**`, `### Post 2 – Instagram`, `LinkedIn:` and
`1 INSTAGRAM`. Fact slots also come in several styles: `[[key]]`, `\[\[key\]\]`, `{{key}}`, or the
value typed out. The slot name is always the fact key. Trap sentences always type the value out, so
the expected label is the evidence label and not `slot_blocked`.

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

| Company (type) | Task scope · publish | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `kestrel-valves` (UK ball-valve maker) | variants=manual, regions=uk · 10-06 | 5-day stock lead time (ends 10-02) | WRAS approval (brass) | "whole KV-50 range is UL Listed" (electric actuator only) | 3-year warranty without "when installed by a qualified engineer" | "ISO 14001 certified" (no_source); "PN63" vs PN40 (conflict) | distributor margin · Severn Water framework price |
| `ledgerly` (SaaS invoicing) | plan_tiers=starter · 10-08 | free Xero sync beta (ends 10-01) | 50% launch discount | 30+ currencies (Pro/Business only) | Starter £12 without "excl. VAT" | "99.99% uptime" (no_source); "SOC 2 Type II" (forbidden, only Type I done) | Type II report date · Acme Freight contract price |
| `brambleworth-home` (homeware shop, own site + Amazon) | channels=own-site, regions=uk · 10-09 | autumn sale 20% (ends 10-05) | summer sale 30% | Prime next-day delivery, 30-day Amazon returns | free delivery over £50 without "UK mainland only" | "award-winning", "4.9 stars from 10,000+" (no_source); "hand-woven" (forbidden) | Black Friday date · Hollis & Grey wholesale price |
| `harbourside-health` (physio & dental) | sites=Quayside · 10-06 | free posture screen (ends 09-30) | £100 off whitening | £49 Millbrook physio price (Quayside is £55) | £39 check-up without "X-rays charged separately" | "NHS approved" (no_source); "FDA approved" (forbidden) | Invisalign floor price · Harbour Port Authority contract |
| `copper-kettle` (café, High St + Riverside) | sites=Riverside, channels=delivery · 10-10 | Deliveroo 20% promo (ends 10-05) | allergen matrix v3 ("milk and mustard") | £12.95 dine-in price in a delivery post; High St 7pm Thursdays | £14.90 Deliveroo price without "Deliveroo prices are higher than in the café" | "Voted the best café", "Free delivery on every order" (no_source) | brunch box food cost · Riverside Studios catering price |
| `fieldstone-advisory` (B2B marketing consultancy) | segments=enterprise · 10-07 | free pipeline audit (ends 10-02) | Google Partner (lapsed 06-30) | £4,500 Growth Sprint (SMB only) | one-client 212% case study without "results from one client, not typical" | "award-winning", "3x ROI" (no_source); "fully automated" (forbidden) | day-rate floor · Castellan retainer |
| `lake-ember-lodge` (lodge + tour desk) | variants=weekend-break · 10-09 | guided kayak tour (season ends 10-04) | Summer Lakes package | "Weekend Break includes a full breakfast" (Midweek Escape only) | £25 transfer without "per person, each way" | "Voted the best lodge", "5-star rated" (no_source) | weekend rate floor · Northfell group rate |

Regulated claims covered: UL Listed only for one variant; SOC 2 Type I only, so "Type II" is
forbidden; "specialist" only for Dr Aisha Rahman (the clean draft uses it correctly, and it must not
be flagged); "FDA approved" is forbidden; an allergen list with a superseded version; the one-client
result disclosure; delivery prices 15% above café prices.

Values from internal and restricted facts never appear in any draft. A pack or a checker that
places them in the pack or in output text is a leak.

## Validate

```bash
python validate_fixtures.py        # stdlib + PyYAML
```

The script checks that every `facts.yaml` has the full Fact v2 shape with allowed enum values; that
every draft has an expected file; that every `contains` and `must_not_flag` substring occurs in its
draft; and that every `fact_key` exists. It also checks that the traps are real: the expired fact is
invalid on `publish_on`, the wrong-scope fact is outside the task scope under the §1 rule, the
disclosure is really missing, clean-draft slots are valid, in scope and public, and no
internal or restricted value appears in a draft.

## Corrections log

**2026-09-29.** The zero-model task-bridge run (`23-eval-suite/results/task-bridge-zero-model-2026-09-29.md`)
reported 8 false warnings. Each flagged line was re-judged only against its company's `facts.yaml` and the
task's `scope` / `publish_on`, not against the checker's output. All 42 drafts were also re-read for the
same kind of gap. Drafts and facts are unchanged. Only `*.expected.yaml` files changed, and each change
carries a `# CORRECTED 2026-09-29:` line. Line numbers refer to the draft `.txt`.

| File (drafts/…expected.yaml) | Draft line | Label · fact_key | Change and reason |
|---|---|---|---|
| `brambleworth-home/03-expired` | 4 `🍁 AUTUMN SALE 🍁` | conflict_or_expired · autumn-sale | added: the heading advertises the autumn sale, which ends 10-05 (publish 10-09). Was a reported false warning. |
| `brambleworth-home/03-expired` | 9 `Subject: Last chance: 30% off garden and outdoor` | conflict_or_expired · summer-sale | added: names the summer sale (ended 08-31). Was a reported false warning. |
| `brambleworth-home/05-missing-disclosure` | 8 `Subject: Free delivery on your autumn favourites` | missing_disclosure · free-delivery-threshold | added: offers free delivery, and "UK mainland only" is absent from the whole draft. Was a reported false warning. |
| `fieldstone-advisory/03-expired` | 8 `Subject: A free pipeline audit for your team` | conflict_or_expired · pipeline-audit-offer | added: names the free audit, which ends 10-02 (publish 10-07). Was a reported false warning. |
| `fieldstone-advisory/04-wrong-scope` | 13 `Six weeks to a healthier pipeline: how our sprint works.` | wrong_scope · growth-sprint-smb | added: the six-week sprint is the SMB-only Growth Sprint, and the task is segments=[enterprise]. Was a reported false warning. |
| `fieldstone-advisory/05-missing-disclosure` | 8 `Subject: 212% more leads` | missing_disclosure · ashgrove-case-study | added: states the one-client result, and "results from one client, not typical" is absent from the whole draft. Was a reported false warning. |
| `fieldstone-advisory/06-invented` | 8 `Subject: Guaranteed pipeline growth` | no_source · null | added: an unsupported guaranteed-result claim (the AI-audit fact forbids guaranteed results). Was a reported false warning. |
| `kestrel-valves/05-missing-disclosure` | 9 `Subject: A manual valve with a 3-year warranty` | missing_disclosure · warranty | added: the email piece states the warranty, and "when installed by a qualified engineer" is absent from the whole draft. Was a reported false warning. |
| `kestrel-valves/05-missing-disclosure` | 5 | missing_disclosure · warranty | `contains` narrowed to `Every KV-50 comes with a 3-year warranty.`: the old substring (`… warranty. No small print`) spanned two sentences, so no single reported sentence could contain it. |
| `lake-ember-lodge/04-wrong-scope` | 7 `Subject: Breakfast by the lake` | wrong_scope · breakfast-included | added (not reported by the run): sells breakfast with the Weekend Break, but breakfast is Midweek Escape only (task variants=[weekend-break]). |
| `lake-ember-lodge/06-invented` | 9 `Subject: The Lake District's favourite lodge` | no_source · null | added (not reported by the run): a popularity/comparative claim with no source, the same kind as "Voted the best lodge". |

Lines that were considered but **not** added, because they name no offer, value or feature from a fact
(only a generic adjective), or because what they say is still true at publish:
`harbourside-health/03` "Subject: Brighter smiles for less"; `kestrel-valves/03` "Subject: Fast delivery on the
manual KV-50"; `kestrel-valves/06` "Subject: The KV-50, now even tougher"; `lake-ember-lodge/03` "Subject: Our
best-value lake stay"; `copper-kettle/04` "Subject: Late cravings? We've got you"; `ledgerly/03` "Subject: Your
books, synced with Xero" (Xero sync still exists as a paid add-on); `ledgerly/04` "Subject: Invoice international
clients from day one" and "clients abroad? Ledgerly Starter has you covered" (invoicing foreign clients in GBP
works on Starter; the 30+ currencies line is the trap); `ledgerly/06` "enterprise-grade reliability" (puffery;
the 99.99% line is the trap); `fieldstone-advisory/05` "That's what Ashgrove Tools achieved…" (carries no number;
the result is in the line before it).
