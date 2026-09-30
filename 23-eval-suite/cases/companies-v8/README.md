# Company fixtures v8 (held out): Phase 1 task bridge

Five **fictional** businesses in industries that no earlier set covers. Each has 10 scoped facts,
one task and six pasted chatbot answers, with the labels a careful checker should give. They test
the Phase 1 path (facts in 05, then a task pack from 88, then a pasted answer, then evidence labels)
with **zero model calls**.

**Everything here is invented.** The companies, people, customers, prices, licence, membership,
report and FHRS numbers, URLs (`*.example`) and phone numbers (Ofcom drama ranges) do not exist.
Real scheme names (Soil Association, TrustMark, NFRC, AAT, AA, Euroclass/EN 13501-1, MTD) appear
only so that the traps are realistic. None of these businesses holds any of them.

**Held out.** The drafts were written from the contracts only (`_dev/phase1-contracts.md` §1, §4, §5)
and the v1 fixture format. Nobody looked at checker code or results while writing them. If a checker
misses a case, fix the checker in general. Do not add a rule for one sentence, and do not edit an
expected file to make it pass.

"Today" is **2026-09-29**. Every task publishes in October 2026, after an offer that is valid today
has ended.

## Layout and scoring

This set uses the same layout, the same six drafts and the same scoring as `../companies/README.md`
(contracts §5). An expected finding is a hit when a reported finding has the same label (and the same
`fact_key`, when one is given) and its sentence contains `contains`. Each `contains` is a verbatim
substring of a single sentence in the draft. `must_not_flag` sentences must get no blocking finding.
Every expected file starts with a `# WHY:` line.

Totals: 30 drafts and 102 expected findings (34 `conflict_or_expired`, 25 `wrong_scope`,
21 `missing_disclosure`, 17 `no_source`, 5 `forbidden_phrase`). There are 165 `must_not_flag`
sentences, and every `01-clean` and `02-paraphrase` draft lists at least 10.

Every trap line in drafts 03 to 06 is listed. Each line that is not listed was checked by hand: it
is true, in scope, and valid on `publish_on`, and any price on it carries its disclosure. A blocking
finding on any unlisted line counts as a false warning.

## What each draft does

| Draft | Content |
|---|---|
| `01-clean` | Slots in several styles (`[[k]]`, `{{k}}`), disclosures written out, and credential numbers and grades exactly as in the facts. Tempting negative controls include questions ("Why save the spa for the weekend?"), headings, hashtags, phone numbers, URLs, video timestamps, and inflected certification wording ("Organic certification covers…", "fire classification"). |
| `02-paraphrase` | No slots. The true values appear in other forms ("Fifty-five pounds", "£34.00 a kilo", "£6.8k", "£95pp", "Sixty-four quid", "four stars" in words, "25-min", "G7729" in brackets). Disclosures appear in equivalent words ("what goes in changes with the seasons", "we confirm the final price after a free survey", "VAT extra", "each"), and questions contain trap words ("Is Quill approved for MTD?", "Certified to Euroclass B-s1,d0?", "best-kept secret?"). None of it may block. |
| `03-expired` | The offer that is valid today but ended before `publish_on` appears **by name** and **by loose description**. Every expiring offer is a "buy N, get one" deal, and serene also brings back an offer that has already ended. |
| `04-wrong-scope` | Uses a true value from another variant, segment or plan: in a price, in a heading or subject, and as a loose "trade prices for everyone" line. |
| `05-missing-disclosure` | The price appears in the **subject line or headline and in the body**, and its disclosure is missing everywhere in any wording. |
| `06-invented` | A look-alike identifier with unusual separators (`G/77-92`, `20-478/51`, `104·97·38`, `731-04-24`, `MPS–FR–2219`), a different grade in words ("five-star", "Platinum Partner", "a Fellow of the AAT", "Class A"), a superlative tied to a place, a review rating, and a staff or service claim. Four companies also have a forbidden phrasing. |

Label conventions used here: a look-alike identifier or a different grade that contradicts a
credential fact is labelled `conflict_or_expired`, with that fact's key. "24/7 support" against
`support-hours` (weekdays, 8am to 6pm) is also `conflict_or_expired`. Superlatives, ratings and
staff or service claims with no fact behind them are labelled `no_source` with a null key.

## Channels (15 pieces, 13 channels)

| Company | Pieces (`task.yaml` channel · how the chatbot headed it) |
|---|---|
| `fernhill-farm-shop` | `whatsapp` (`*📱 WhatsApp broadcast*`, `=== 1 WHATSAPP ===`, `📲 WhatsApp message`), `sms` (`*💬 SMS (160 characters)*`, `**2. SMS**`, `📱 SMS`), `email` (`📧 Email`, `**Email**`; **no heading** in 01) |
| `apex-roofing` | `google_business` (`### Google Business Profile – Update`, `**1. Google Business update**`), `flyer` (`### Flyer (A5, front and back)`, `**Flyer**`), `website` (`**3. Website page**`; **no heading** in 01, 04 and 06, where the piece starts at `Page title:` or `Title:`) |
| `quill-bookkeeping` | `linkedin` (`**LinkedIn post**`, `1 LINKEDIN`, `## LinkedIn`), `youtube` description (`**YouTube description**`, `2 YOUTUBE`), `email` (`3 EMAIL`, `## Email`; **no heading** in 01 and 06) |
| `serene-spa-hotel` | `instagram` (`📸 Instagram caption`, `Instagram:`), `pinterest` (`📌 Pinterest Pin`, `Pinterest:`), `newsletter` (`Newsletter:`, `**Newsletter**`; **no heading** in 01 and 06) |
| `metro-print-signs` | `tiktok` script (`🎬 TikTok script (30 seconds)`, `TikTok:`, lines start with `VO:`), `facebook` (`📘 Facebook post`, `Facebook:`), `email` (`Email:`; **no heading** in 01, 04 and 06) |

Every company's 03 and 05 use the `=== N CHANNEL ===` markers from the pack.

## Traps per company

| Company (type) | Task scope · publish | Expired at publish (valid today) | Scope trap (04) | Segment-limited price | Missing disclosure (05) | Credential (id + grade) | Non-numeric inclusion | Invented / forbidden (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|---|---|
| `fernhill-farm-shop` (farm shop & butcher) | segments=retail · 10-09 | Sausage Stack "buy 3 packs, get the 4th free" (ends 10-04) | £9.20/kg trade mince, "trade price list" | `trade-mince` (trade) | £55 Autumn Meat Box without "contents vary with the season" | FHRS rating 4 (Good), ID 1184520; Soil Association organic licence G7729 (beef and lamb only) | box: recipe card + stock bones | best butcher in Leicestershire; 4.9 on Google; `G/77-92`; "five-star" hygiene; master butchers with 40 years' experience; forbidden "everything we sell is organic" | mince cost · Old Forge Inn sirloin price |
| `apex-roofing` (roofing contractor) | regions=bristol, segments=homeowner, variants=pitched-roof · 10-12 | Gutter Trio "book 2 cleans, 3rd free" (ends 10-10) | 25-year guarantee (flat roofs only); £85 inspection | `landlord-rate` (landlord) | "from £6,800" without "final price confirmed after a free survey" | TrustMark 2047815; NFRC Full Contractor Member 31972 | scaffolding, skip, clear-up | Bristol's number one roofer; 4.9 on Checkatrade; `20-478/51`; "NFRC Platinum Partner"; 24-hour call-out; forbidden "NFRC approved" | margin floor · Hollybrook framework rate |
| `quill-bookkeeping` (bookkeeping SaaS + service) | plan_tiers=sole-trader, segments=self-employed · 10-14 | Switch & Save "pay for 2 months, 3rd free" (ends 10-10) | £42 Small Business plan and payroll; £21 charity price | `charity-price` (charity) | £18 Sole Trader price without "plus VAT" (no VAT anywhere) | AAT licence 1049733, led by Priya Doshi MAAT | free onboarding call + record import | UK's best-loved app; 4.8 on Trustpilot; 24/7 support (conflicts with support hours); "a Fellow of the AAT"; `104·97·38`; forbidden "chartered accountants" and "HMRC approved" | monthly churn · Harlow Dental Group price |
| `serene-spa-hotel` (spa hotel) | segments=day-guest, variants=weekday-spa-day · 10-16 | Friends Go Free "book 3, the 4th goes free" (ends 10-12); Summer Twilight Spa already ended 08-31 | £125 Weekend Spa Day and its 55-minute treatment; £79 member price | `member-price` (member) | £95 without "per person" (and no each, pp or per guest) | AA Four Star Hotel, ID 7310442 | robe, slippers, lunch, thermal suite and pool | most luxurious spa in Yorkshire; "five-star hotel"; 9.6 on Booking.com; award-winning therapists; personal butler; `731-04-24` | cost per guest · Beacon Insurance rate |
| `metro-print-signs` (sign maker) | segments=retail, variants=acm · 10-15 | A-Board Autumn Deal "buy 2, 3rd free" (ends 10-11) | £38 Foamex board; 15% trade discount | `trade-discount` (trade) | £64 A1 panel without "ex VAT" (no VAT anywhere) | Euroclass B-s1,d0, report MPS-FR-2291 | free digital proof + fixing kit | South London's fastest; 4.9 on Trustpilot; "Class A"; `MPS–FR–2219`; overnight design team; same-day London installation; forbidden "fireproof" | Foamex material cost · Kingsmere framework price |

Values from internal and restricted facts never appear in any draft. If a pack or a checker puts them
in the pack or in its output, that is a leak.

## Validate

```bash
python ../companies-v6/validate_fixtures.py .     # stdlib + PyYAML; prints "5 companies, 50 facts, 30 drafts, 0 problems"
```

The validator checks the full Fact v2 shape and enums, and that every `contains` and `must_not_flag`
substring is in its draft. It checks that every trap is real: the expired fact is invalid at
publish, the wrong-scope fact is out of scope under the §1 rule, the disclosure is absent, and
clean-draft slots are valid, in scope and public. It also checks that no internal or restricted value
leaks into a draft.
