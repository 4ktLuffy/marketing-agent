# Company fixtures v6 (held out): catching traps AND not crying wolf

Five more **fictional** businesses, in industries the earlier sets do not cover. Each has scoped facts,
one task and six pasted chatbot answers, with the labels a careful checker should give. The layout,
the contracts (`_dev/phase1-contracts.md` §1, §4, §5) and the scoring rules are the same as in
`../companies/README.md`.

**Everything here is invented.** The companies, people, clients, prices, membership numbers,
certificate IDs, award schemes (for example "Accessible Coast") and contracts do not exist. Some real
bodies and sites are named (BPCA, RSPH, CQC, Cytech, Google Partners, Trustpilot, Clutch, Tripadvisor)
only as the kind of scheme a real business would cite. Nothing here says anything about those bodies.

**Held out.** These files were written from the contracts only, without reading any checker code
or earlier results. Whoever builds or tunes the checker must not read them. If the checker misses a
case, fix the checker in general. Do not add a special rule for one sentence, and do not edit an
expected file so that it passes.

"Today" is **2026-09-29** for every company. Every task publishes in October 2026, after at least one
fact that is valid today has expired.

## What is different: the negative controls carry half the weight

Earlier sets mostly asked "does the checker catch the trap?". This set asks the same question
**and** "does it stay quiet when the copy is true?". A checker that blocks every price, every
seasonal word or every mention of a certificate would score well on traps and still be useless.

So `01-clean` and `02-paraphrase` are written to tempt a checker into a false alarm. Every sentence
in them is **true, valid at `publish_on` and in scope**:

- Normal marketing copy that names the business's own product categories, with questions ("Thinking
  about a new bike?", "Over 60?") and headings (`**What does a week at Alder Court cost?**`, `## Time for a service?`).
- True values written in other formats: `1200 pounds` / `£1,200.00` for £1,200; `145 quid` /
  `£145.00` for £145; `1,250 pounds a week` / `£1250 per week`; `fourteen pounds` / `Fourteen quid`
  for £14; **`a tenner`** for the £10 senior fare; `64 per cent`; `99.9 percent`; `ninety days`;
  `nine in the morning until eight at night`; `05/10/2026 until 28/03/2027`; `membership number 10472`
  where the fact says `no. 10472`.
- Harmless look-alike words next to real traps: "autumn" / "this season" / "summer" as plain season words
  in sets with an expired autumn or summer offer; "ground **level**" next to RSPH Level 2; "a
  **good** cup of tea" and "Good food and good company" next to the CQC rating Good; "**Partnering**
  with a small studio" next to Google Partner; "pest-free this season" next to the forbidden
  "guaranteed pest-free forever".
- Required disclosures are present, sometimes in the same sentence and sometimes elsewhere in the
  same piece.

Each `01-clean` and `02-paraphrase` lists **at least 6** such sentences in `must_not_flag` (81 in all,
against 76 expected trap findings). Report false warnings on these separately from misses. The
plan gate still applies: 0 blocking findings on 01/02, and at most 1 non-blocking `review` per clean draft.

The v1 validator only allows 2 to 4 `must_not_flag` sentences in `01-clean`. For that reason this folder has its own
copy, `validate_fixtures.py`. It differs from `../companies/validate_fixtures.py` only in that rule, which
here is at least 6 for both 01 and 02. With the v1 script, these five "2-4 sentences" failures are the
only problems it reports.

```bash
python validate_fixtures.py                                 # 5 companies, 50 facts, 30 drafts, 0 problems
python ../companies/validate_fixtures.py .                  # same, except the 2-4 count rule
```

## Drafts

| Draft | What it does | Expected |
|---|---|---|
| `01-clean` | true, in scope, disclosures present, slots in several styles, tempting wording | `blocked: false`, ≥6 `must_not_flag` |
| `02-paraphrase` | the same facts freely reworded and reformatted, no new claims | `blocked: false`, ≥6 `must_not_flag` |
| `03-expired` | an offer valid today that ends before `publish_on`, **also given in words only**, plus an already-expired fact where one exists | `conflict_or_expired` |
| `04-wrong-scope` | a true value for another site, segment, plan tier or season | `wrong_scope` |
| `05-missing-disclosure` | a fact without its required disclosure, in the **subject line and the body** | `missing_disclosure` |
| `06-invented` | a look-alike identifier, a different grade on a named scheme, a superlative, a rating, a staff credential | `conflict_or_expired` / `no_source` / `forbidden_phrase` |

The headings vary: `=== 1 FACEBOOK ===`, `**1. Facebook**`, `### Post 1 – Instagram`, `Instagram:`,
`1 LINKEDIN`, `## Email`. In some drafts the last piece has no heading and starts straight at `Subject:`
(willow 02 and 05, forge 04, prism 02 and 05, tideway 05). Trap sentences always type the value out, so
the expected label is the evidence label and not `slot_blocked`. Membership and workshop numbers are
written as "number 10472" in the drafts, so an abbreviation like "no." cannot break a sentence in two.

## Traps per company

| Company (type) | Task scope · publish | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `cobalt-pest-control` (pest control) | segments=domestic · 10-08 | 15% off mouse treatments (ends 10-05); in words: "take a slice off the usual price" | £50 summer wasp nest | £45/month protection plan (commercial only) | £145 rat treatment without "up to 3 visits" | BPCA no. 10427 (real 10472); RSPH Level 3 (real Level 2); "number one"; 4.9 Trustpilot; in-house entomologist | technician cost per visit · Hollowbrook contract |
| `willow-care-homes` (care homes, 2 sites) | sites=alder-court · 10-12 | half-price first respite week (ends 10-05); in words: "half the usual fee for the first seven days" | none | "Outstanding" CQC rating (Willow House; Alder Court is Good) | weekly fees without "subject to a care needs assessment" | CQC location ID 1-4471029358 (real …385); "rated Excellent" (no such CQC grade; Good); "best care home"; 9.8/10 reviews; on-site GP | agency nurse cap · council contract rate |
| `forge-cycles` (bike shop, 2 stores) | sites=canal-street · 10-07 | £90 Full Service / save £20 (ends 10-04); in words: "knock twenty quid off" | 10% off all hybrids | Tempo 3 size S in stock (Quay Street only) | £110 Full Service without "parts charged extra" | Cytech workshop 2219 (real 2291); Cytech Technical Three (real Two); "biggest bike shop"; 5 stars on Google; in-house physiotherapist | labour cost · council fleet contract |
| `prism-web-studio` (web agency) | plan_tiers=starter · 10-14 | first month of hosting free (ends 10-10); in words: "host your new site for nothing", "won't charge you a penny" | none | online booking integration (Growth only) | £1,800 Starter without "excl. VAT" | Partner ID 518-204-7713 (real …731); "Premier Partner" (forbidden_phrase); "fastest web studio"; 5.0 on Clutch; certified accessibility specialist | day-rate floor · Marlow & Finch retainer |
| `tideway-ferries` (ferry & day trips) | sites=gull-island, variants=winter · 10-10 | summer timetable, 8 sailings a day (ends 10-04); bring-a-friend offer (ends 10-04); in words: "two for the price of one", "companion's ticket costs nothing" | none | £18 adult return (summer season; winter is £14) | £10 / "a tenner" senior fare without "proof of age" | certificate AC-0874 (real AC-0847); Accessible Coast Platinum (real Gold); "fastest crossing"; 4.9 Tripadvisor; paramedic crew | fuel-surcharge trigger · school group contract |

Other things the sets test: the consented one-client case study (`oakleaf-case-study`) needs its disclosure,
and the clean drafts keep it. `rodent-guarantee` forbids "lifetime guarantee". `google-partner` forbids
"Premier Partner". In `tideway-ferries` the season is a scope dimension (`variants`), so the £18 summer fare is
wrong_scope and not expired, while the summer **timetable** is bounded by dates and so is expired.

Values from internal and restricted facts never appear in any draft. A pack or a checker that places them
in the pack or in its output text is a leak.

## Lines checked by hand and left unlisted

Every non-blank line of every draft was checked against its company's `facts.yaml`, the task scope and
`publish_on`. Lines not named in an expected file are greetings, headings, sign-offs, chatbot
preambles and closings, slot lines in clean drafts (valid, in scope, public, disclosure present), and these
generic lines that name no value, offer, grade or feature from a fact:
`cobalt/04` "Tired of calling us out every time?"; `cobalt/06` "Subject: Pest control you can trust";
`willow/05` "Subject: Clear, simple fees at Alder Court" (names fees, gives no amount); `willow/06`
"Subject: Care you can count on"; `forge/06` "Subject: Serious about bikes"; `prism/01` "Design, build and
launch happen in one place, with one team you can call."; `prism/06` "Subject: Websites that win";
`tideway/01` "Grey skies, a flask of something hot and the sea air: that's our kind of winter trip.";
`tideway/06` "Subject: Your winter trip to Gull Island". A blocking finding on one of these counts as a
false warning. A non-blocking `review` does not.

In `tideway/06` the Platinum and AC-0874 sentences also leave out the wheelchair booking note. An extra
`missing_disclosure` on those already-expected sentences is allowed and does not count as a false warning.
