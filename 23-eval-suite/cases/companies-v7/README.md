# Company fixtures v7 (held out): five new industries

This set has five **fictional** businesses in industries that no earlier set covers: B2B dental supplies,
a driving school, a law firm, a furniture maker, and a two-site children's nursery. Each company has 10 scoped
facts, one task, and six pasted chatbot answers with the labels a careful checker should give. The layout,
draft kinds, labels and scoring are the same as in `../companies/README.md` (contracts
`_dev/phase1-contracts.md` §1, §4, §5).

**Everything here is invented.** The companies, people, clients, councils, prices, certificate and register
numbers (SRA, ADI, Ofsted URN, FSC, DoC) and URLs (`*.example`) do not exist. Any resemblance to a real business,
licence or register entry is accidental.

**Held out.** These files were written from the contracts only, without looking at any checker code, any
results, or any earlier company set other than the v1 format example. Do not tune a checker to them. If a
checker misses a case, fix the checker in general. Do not add a rule for one sentence, and do not edit an
expected file so that it passes.

"Today" is **2026-09-29**. Every task publishes in October 2026, after one fact that is valid today has expired.

## What is harder than in earlier sets

- **Rich negative controls.** `01-clean` and `02-paraphrase` each list 8 to 12 `must_not_flag` sentences. The
  true values appear in other formats and words ("Forty-four pounds", "£44/hr", "eight hundred and ninety-five
  pounds", "8–10 weeks", "£250k", "five o'clock"). They also include questions, headings, and look-alike words
  that name no offer, tier or rating: "Stock up for winter" (the offer is Autumn Stock-Up), "Gold-standard
  service" (gold accounts), "Summer or winter" (Summer Clearance), "Autumn is a busy time to move" (Autumn
  Movers), "Walnut or oak?", "a free care kit" (not the local free delivery), "Good food" (Ofsted Good),
  "Settling in takes time" (Free Settling-In Week), and "DVSA-approved driving instructor" (the forbidden
  phrase is "DVSA approved driving *school*").
- **Disclosure in equivalent words.** In every company, the disclosure that `05` leaves out appears in `01-clean`
  at least once in different words. The checker must accept that wording:

  | Company | Required disclosure | Equivalent wording in `01-clean` |
  |---|---|---|
  | harbour-dental-supplies | excluding VAT | "plus VAT" (LinkedIn) |
  | kestrel-driving-school | 146 first-time tests, Sep 2025 to Aug 2026 | "based on 146 first-time tests taken between September 2025 and August 2026" |
  | lumen-solicitors | excluding VAT and disbursements | "plus VAT and disbursements" (LinkedIn, blog) |
  | oakwood-furniture | when registered within 30 days of delivery | "just register it within 30 days of delivery" |
  | sparrow-nursery | before funded hours | "before any funded hours are taken off" (Facebook) |

  In `05`, the disclosure appears nowhere, in any wording. This covers the subject line and the body.
- **Headings and splitting.** Pieces are marked in several ways: `=== 1 LINKEDIN ===`, `**1. Facebook**`,
  `### Post 1 – LinkedIn`, `LinkedIn:`, `1 INSTAGRAM`, `## Facebook post`, `Option 1 — Facebook`, and
  `🔹 Piece 1: Facebook`. In some drafts the last piece has no heading at all: the lumen blog in 01, the oakwood
  pin in 01, the sparrow Google update in 01 and 02, the kestrel WhatsApp message in 02, and the dental email in
  03 (it starts at `Subject:`). Slots appear as `[[key]]` and `{{key}}`, and only in the negative controls.
  Trap sentences always type the value out.
- **06 invented claims, the same five kinds in every company.** Each `06` draft has five kinds of invented claim:
  - an identifier that looks like the real one but is written in an unusual format (`conflict_or_expired`);
  - a different grade or rating, written in words (`conflict_or_expired`);
  - a superlative tied to a place (`no_source`);
  - a rating with no source (`no_source`);
  - a claim about staff or service with no source (`no_source`).

  Each also uses one forbidden phrase (`forbidden_phrase`).

## Traps per company

| Company (type) | Task scope · publish | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `harbour-dental-supplies` (B2B dental consumables) | segments=standard-account, regions=england-wales · 10-08 | Autumn Stock-Up, free box of gloves over £250 (ends 10-03) | Summer Clearance, 25% off prophy paste | £4.70 gold-account glove price (standard is £5.40); 1pm Scotland/NI cut-off (E&W is 5pm) | £5.40 without "excluding VAT"; 5pm cut-off without "Monday to Friday" | "HDS/DOC/2219" (DoC HDS-DOC-2291); "Class Two" (Class I); "UK's fastest"; "4.8 out of 5"; "dental nurse answers every call"; "FDA approved" (forbidden) | landed cost · Bayview framework price |
| `kestrel-driving-school` (driving lessons) | variants=automatic, segments=learners, regions=norwich · 10-12 | Autumn Block Deal, 11th hour free (ends 10-04) | 5-day Summer Intensive | £38 manual price (automatic is £44); £48 refresher price (full-licence holders) | 71% pass rate without the cohort "146 first-time tests, Sep 2025 to Aug 2026" | "417-358" (ADI 417385); "grade six" (grade A); "highest pass rate in Norfolk"; "5-star on Google"; "every instructor grade A"; "DVSA approved driving school" (forbidden) | franchise fee · Wensum College rate |
| `lumen-solicitors` (law firm, Bristol + Bath) | sites=bristol, variants=up-to-250k · 10-13 | Autumn Movers, £100 off (ends 10-05), incl. derived £795 | — | £1,195 band-2 fee (£250,001 to £500,000); Saturday opening (Bath office only) | £895 without "excluding VAT and disbursements" | "8045/71" (SRA 804517); "Lexcel Gold" (Lexcel v6.1, no tiers); "Bristol's number one"; "4.9 out of 5"; "every file handled by a partner"; "no hidden costs" (forbidden) | cost rate · Severnside panel fee |
| `oakwood-furniture` (furniture maker + showroom) | variants=oak, regions=national · 10-14 | Autumn Sale 15% off oak tables (ends 10-06), incl. derived £1,232.50 | — | free delivery and assembly (within 30 miles of the showroom only); £1,890 walnut price | 10-year guarantee without "when registered within 30 days of delivery" | "FSC® licence C-152107" (FSC-C151207); "FSC Recycled" (FSC 100%); "Shropshire's finest"; "five stars by 1,000+"; "single master craftsman"; "carbon neutral" (forbidden) | oak board cost · Pembridge Hotels price |
| `sparrow-nursery` (nursery, Elm Road + Canal Street) | sites=canal-street, segments=2-year-olds · 10-09 | Free Settling-In Week (closes 10-03) | — | Ofsted Outstanding (Elm Road; Canal Street is Good); 7:30am to 6:30pm (Elm Road hours) | £66 full day without "before funded hours" | "EY-574481" (URN EY574418); "Excellent" (Good); "best nursery in Hartfield"; "9.8 out of 10"; "every practitioner a qualified teacher"; "free childcare" (forbidden) | staff cost · council placement rate |

Each company also has three more kinds of fact:

- a credential or certification with an identifier and a grade or rating: DoC plus Class I, ADI number plus
  grade A, SRA number plus Lexcel v6.1, FSC licence plus FSC 100%, and Ofsted URN plus rating;
- an inclusion with no number: a named account manager, free pick-up, a named solicitor plus case tracking, a
  care kit, and meals plus nappies;
- a price limited to one segment or variant.

Internal and restricted values never appear in any draft. A pack or checker that puts them into the pack or
the output text is a leak.

## Hand check of unlisted lines

Every non-empty line of every draft was read. The lines that are not named in `findings` or `must_not_flag` are
only chatbot preambles and sign-offs, piece headings, greetings and courtesy lines ("Thank you for joining our waiting list"), hashtags, lines that contain a slot, and one generic sentence in the lumen `01` blog ("Your conveyancer handles the legal side of buying…"). No
unlisted line states a value, an offer, a rating or a claim. `kestrel-driving-school/04` notes one such line in
its expected file ("Subject: Back behind the wheel" names no price or segment).

## Validate

```bash
python ../companies-v6/validate_fixtures.py .      # stdlib + PyYAML; checks this folder
```
