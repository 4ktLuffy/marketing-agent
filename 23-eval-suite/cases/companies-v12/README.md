# Company fixtures v12 (held out) — Phase 1 task bridge

Five **fictional** businesses in industries not used by earlier sets, each with ten scoped facts, one
task with three pieces and six pasted chatbot answers with the labels a careful checker should give.
They test the Phase 1 path (facts in 05 → task pack from 88 → pasted answer → evidence labels) with
**zero model calls**.

**Everything here is invented.** The companies, people, towns, clients, prices, certificates, schemes,
labs, awards and URLs (`*.example`) do not exist. Any resemblance to a real business is accidental.
Where a real scheme name appears (Cyber Essentials Plus, NHS flu service, ISO 27001, UK low-alcohol
labelling), it is only there to make a regulated trap realistic; nothing here says any real body has
certified any of these companies.

**Held out.** Whoever builds `88-task-bridge`, `44-claim-checker` or the 05 fact store must not read
these files or tune code to them. They were written from the contracts only
(`_dev/phase1-contracts.md` §1, §4, §5) and the v1 format (`../companies/`), without looking at any
checker code, results or other fixture sets. If a checker misses a case, fix the checker in general.
Do not add a special rule for one sentence here, and do not edit an expected file so that it passes.

"Today" for every company is **2026-09-29**. Every task publishes in October 2026, after one fact that
is valid today has expired.

## What is harder than v1

- **Loose wording in traps.** The wrong fact is rarely written the way `value_text` writes it:
  "twelve quid", "on the house", "knock a quarter off", "half the usual price", "for nothing",
  "under a hundred quid", "the second stein's on us", "all year round", "every branch", "either shop",
  "around the clock", "day and night, weekends included", "for the pair of you", "at the bar",
  "won't cost you a penny more", "anywhere in the country".
- **Everyday wording in the negative controls.** Paraphrases keep every fact true and every disclosure
  present, but in other words: number words ("thirty", "sixteen pounds", "half a percent"),
  abbreviations ("18+", "+VAT", "Sat 9-1", "4-hr"), synonyms ("plus VAT" / "VAT on top" for
  "excl. VAT", "if you qualify" for "if you're eligible", "Once you've booked with us" for
  "with a confirmed booking", "Newcomers" for "for new students") and moved clauses ("Tested below
  20ppm and brewed with barley, Low Weir is…", "Of the 142 graded exams our students sat in 2025,
  96% were passes"). A checker that only matches the literal disclosure string will raise false
  warnings here; that is the point.
- **Missing disclosures written loosely.** "Sixteen pounds gets you…", "Thirty-five quid per user a
  month", "Ninety-six percent of our students…".
- **Regulated traps.** A pharmacy (regulated_health: age limit, NHS eligibility, "guaranteed
  protection", an independent prescriber at one branch only), a brewery (regulated_food: a 0.5% ABV
  beer that UK rules call low alcohol, not alcohol-free; a barley lager tested below 20ppm that may be
  called gluten-reduced but not gluten-free), an MSP (security: Cyber Essentials Plus, not ISO 27001,
  never "unhackable"), a removals firm (a trade scheme that is not "government approved", insurance
  that is capped, not "fully insured for everything"), and a music school (a result claim that needs
  its base, never "every student passes").

## Layout

```
<slug>/facts.yaml        {company: {name, type, today}, facts: [Fact v2 ...]}     (contracts §1)
<slug>/task.yaml         {goal, pieces, scope, publish_on, audience, notes}        (POST /tasks, §4)
<slug>/drafts/NN-name.txt             the pasted chatbot answer
<slug>/drafts/NN-name.expected.yaml   {blocked, findings[{contains,label,fact_key}], must_not_flag}  (§5)
```

Each `facts.yaml` has exactly ten facts: one `internal`, one `restricted`, one valid today that expires
before `publish_on`, one already `expired`, at least one scoped outside the task (the 04 trap), facts
with `conditions`, `required_disclosures`, `forbidden_phrasing` and a non-`none` `claim_class`.

The six drafts are the same for every company:

| Draft | What it does | Expected |
|---|---|---|
| `01-clean` | correct, in scope, all disclosures present, slots and typed values | `blocked: false`, no blocking finding |
| `02-paraphrase` | the same facts in everyday words, no new numbers (negative control) | `blocked: false` |
| `03-expired` | uses a fact that is expired on `publish_on` (and one already expired), loosely worded | `conflict_or_expired` |
| `04-wrong-scope` | uses a true fact from another site, plan, variant, channel, region or segment | `wrong_scope` |
| `05-missing-disclosure` | uses a fact but leaves out its required disclosure everywhere in the draft | `missing_disclosure` |
| `06-invented` | invents an award, rating, result or credential, or uses a forbidden phrasing | `no_source` / `forbidden_phrase` |

The drafts are written the way the free ChatGPT, Claude and Gemini chats answer: a preamble (or none),
markdown bold and headings, emojis, separators and a closing offer. Headings vary: `=== 1 FACEBOOK ===`,
`**Facebook post**`, `1) Facebook`, `**Option 1 – Facebook**`, `FACEBOOK —`, `Option 1`, `1 YOUTUBE`,
`### YouTube`, `📸 Instagram`, `WhatsApp message —`, `WHATSAPP:`, and several drafts with no preamble or no sign-off.
Fact slots come as `[[key]]`, `\[\[key\]\]`, `{{key}}`, or the value typed out. Trap sentences always
type the value out, so the expected label is the evidence label and not `slot_blocked`.

## Scoring (contracts §5)

Same as `../companies/README.md`: an expected finding is hit if a reported finding has the same `label`
(and `fact_key`, when given) and its sentence contains `contains`; `must_not_flag` sentences must get no
blocking finding; blocking findings on unlisted sentences are false warnings; `blocked` must match.
When one sentence carries two traps (hartwell 05 website line), it is listed once per fact key. An
extra blocking label on a sentence that is already expected (for example a missing disclosure on a
sentence that is also a forbidden phrase) does not count as a false warning.

Each expected file begins with a one-line `# WHY:` comment that explains the label.

## Traps per company

| Company (type) | Task scope · channels · publish | Expired at publish (valid today) | Already expired | Scope trap (04) | Missing disclosure (05) | Invented / forbidden (06) | Internal · restricted |
|---|---|---|---|---|---|---|---|
| `alder-finch-pharmacy` (community pharmacy, 2 branches; multi-site, regulated health) | sites=Castle Green · facebook, sms, google_business · 10-12 | £12 online early-bird flu jab (ends 10-04): "only twelve quid", "online for £12" | free travel health consultation (ended 06-30): "on the house" | Market Street 8pm opening as "Every branch … till eight", "open till 8pm weekdays"; Market Street prescriber at "either shop" | £16 flu jab without "adults 18 and over" ("Sixteen pounds gets you…"); free delivery without "within 3 miles of the branch" | "rated 4.9 stars by over 800 patients" (no_source); "Free flu jabs for everyone", "guaranteed protection" (forbidden) | 1,200-dose vaccine order · Hollybank House care-home price |
| `kittiwake-cyber` (managed security provider; B2B, plan tiers) | plan_tiers=essentials, segments=smb · linkedin, email, x · 10-14 | free phishing simulation (ends 10-09): "for nothing", "Free phishing test" | 50% off onboarding (ended 08-31): "half the usual price" | 24/7 SOC (Guard/Sentinel only) as "around the clock, all year round", "day and night, weekends included", "24/7" | £35 Essentials and £450 onboarding without "excl. VAT" ("Thirty-five quid per user a month") | "ISO 27001 certified", "unhackable" (forbidden); "99.7% fewer successful attacks", "top managed security provider" (no_source) | Essentials discount floor · Harrowgate Legal Sentinel price |
| `gristmill-brewing` (craft brewery, taproom + webshop; regulated food) | channels=taproom · instagram, tiktok, newsletter · 10-16 | Oktoberfest 2-for-1 steins (ends 10-04): "the second stein's on us" | Friday live music (ended 08-28): "all year round" | webshop £36 mixed case with free delivery sold "at the bar" | Tailrace "low-alcohol IPA" without "0.5% ABV"; Low Weir "gluten-reduced" without "brewed with barley" | "gluten-free", "alcohol-free" (forbidden); "Gold medal … Northern Beer Awards" (no_source) | hop contract cost · Harlow Inns keg price |
| `hartwell-removals` (removals, home and business) | regions=local, segments=home · whatsapp, flyer, website · 10-15 | 25% off Tue–Thu moves (ends 10-07): "knock a quarter off" | £99 student man-and-van (ended 09-20): "under a hundred quid" | £2.10/mile national rate ("anywhere in the country"); business-only weekend moves at no extra charge ("won't cost you a penny more") | "from £495" without "price confirmed after a free home survey"; 30 boxes without "with a confirmed booking" ("for every customer") | "Voted … No.1", "12,000 happy moves since 1998" (no_source); "fully insured for everything", "Government approved" (forbidden) | crew day cost · Brightmoor Relocation rate |
| `linnet-music-school` (music school; variants) | variants=group · youtube, threads, blog · 10-19 | £99 spring term if enrolled by 10-10: "under a hundred quid", "save £21" | Summer Rock Camp (ended 08-28): "back for half term" | parent & child £150 "for the pair of you"; one-to-one £32 "for half an hour" | £120 term without "10 weekly lessons"; 96% pass rate without "based on 142 exams taken in 2025" ("Ninety-six percent…") | "Every student passes" (forbidden); "best-rated … 300+ five-star reviews", "conservatoire-trained", "DBS-checked" (no_source) | tutor pay rate · St Aldric's Primary contract |

Coverage: B2B (`kittiwake-cyber`), multi-site (`alder-finch-pharmacy`), plan tiers (`kittiwake-cyber`),
regulated health (`alder-finch-pharmacy`), regulated food (`gristmill-brewing`), security
(`kittiwake-cyber`), safety/trade certification (`hartwell-removals`), result claim
(`linnet-music-school`). Scope dimensions used by traps: sites, plan_tiers, channels, regions, segments,
variants.

Values from internal and restricted facts never appear in any draft. A pack or a checker that places
them in the pack or in output text is a leak.

## Validate

```bash
cd 23-eval-suite && python cases/companies-v6/validate_fixtures.py cases/companies-v12   # stdlib + PyYAML
```

Result when written (2026-09-30): `5 companies, 50 facts, 30 drafts, 0 problems`.
