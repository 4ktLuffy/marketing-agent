# Eval scoreboard (task bridge, zero model)

Generated from `23-eval-suite/results/*.json` by `python -m evalsuite.scoreboard` (2026-10-07). Every
set was written by a separate agent that never read the checker code. **First seen** = the run before
any fix used that set; **after fixes** = the current code (`final-*` runs). "Traps caught" counts expected
findings (sentence + label + fact); "false alarms" are blocking findings on other sentences; "truthful
blocked" are blocking findings on sentences marked must-not-flag.

## Real businesses (facts read from an ERP, read-only; the data itself stays private)

Each real set was held out twice: from the old code (commit 52f0d74) and from the new code
at the moment it was first scored (the fixing agents never saw it).

| Held-out real set | Old code (commit 52f0d74) | New code, first time the set was seen | False alarms / truthful blocked (old → new) |
|---|---|---|---|
| real-hotel-1 (hotel, set 1) | 52/70 (74%) | 58/70 (83%) | 6/4 → 6/3 |
| real-drinks-1 (drinks distributor) | 35/62 (56%) | 47/62 (76%) | 1/1 → 1/2 |
| real-hotel-2 (hotel, set 2) | 48/72 (67%) | 53/72 (74%) | 3/4 → 3/1 |
| real-mixed-3 (both, local-language lines) | 27/59 (46%) | 50/59 (85%) | 1/6 → 1/2 |
| real-4 (both) | 33/81 (41%) | 65/81 (80%) | 1/3 → 1/4 |
| real-5 (both) | 44/90 (49%) | 68/90 (76%) | 1/12 → 1/11 |
| real-6 (both, invented extras) | 48/103 (47%) | 84/103 (82%) | 0/0 → 0/0 |
| real-7 (both, extras + fact traps) | 40/118 (34%) | 77/118 (65%) | 4/10 → 5/1 |
| real-8 (both, new invented-extra kinds) | 56/162 (35%) | 105/162 (65%) | 0/2 → 0/0 |
| real-9 (both, passive-voice extras, CTAs everywhere) | 55/203 (27%) | 122/203 (60%) | 0/0 → 1/0 |
| real-10 (both, true totals, rounded internal values) | 59/206 (29%) | 137/206 (67%) | 1/8 → 1/8 |
| **All 11 real sets** | **497/1226 (41%)** | **866/1226 (71%)** | **18/50 → 20/32** |

**End to end with a real chatbot** (a separate model answering real packs from today's facts, plainly and
nudged to "add perks"; pasted back and submitted): before the new fixes 16 of 16 pieces were blocked,
mostly falsely (slots used as citations, the pack's own grouped room lines, correct totals). After: 2 of 16,
both correct (the chatbot wrote [[missing: group rates]]). A manual read of the 14 passing pieces found one
subtle slip ("delivers" where the facts say "supplies").

**Small model as the chatbot** (Haiku; free chatbots are usually small): it invents far more. Two held-out
rounds, labelled by hand before the checker ran:
- Round A (first sight): 0 of 6 hard errors caught, 3 false blocks ("Other 33cl" read as a brand, "the best of
  <our town>", "margins healthy"). After fixing on it: 5 of 5 (one label dropped as debatable), 0 false blocks.
- Round B (first sight, after round A's fixes): 2 of 3 hard errors (kit: health framing, missing under-21
  line; missed "designated drivers"), 0 of 7 medium (urgency, rivals, draught), 3 kinds of false block.
  After fixing on it: 3 of 3 hard, 6 of 7 medium, 1 debatable label.
The pattern is clear: each new round of a small model finds new things, first sight is far below the
after-fix number, and a person must approve every post.

Notes: real-hotel-1's "new code" column is after the first real-data fix round (the set had not been
used for it). real-drinks-1 uses a confirmed local alcohol-advertising starter kit (kept private);
before, no such kit existed. Two real-drinks-1 expected labels look wrong (an expired price marked
must-not-flag; "happy hour" marked must-not-flag while the kit forbids it) and are left as written.
real-7's first-seen false alarms include 3 fixture omissions: subject lines with an unsourced claim
("Win a trip with your first order", "The best birding lodge in the country", "The region's most trusted
retreat venue") that the fixture's own notes call traps but did not list. They are counted as false
alarms anyway.

## All sets: first seen vs after fixes

| Set | Businesses | First seen: traps caught | First seen: false alarms / truthful blocked | After fixes: traps caught | After fixes: false alarms / truthful blocked |
|---|---|---|---|---|---|
| companies-v2 | education, manufacturing, beauty_clinic, logistics, real_estate | 52/67 (78%) | 0 / 0 | 67/67 (100%) | 0 / 0 |
| companies-v3 | fitness, food_producer, veterinary, energy_installer, it_services | 53/69 (77%) | 1 / 2 | 92/92 (100%) | 0 / 0 |
| companies-v4 | boat dealer and servicing yard, event and conference venue, dental laboratory (B2B), commercial printer and packaging, accountancy firm | 70/77 (91%) | 0 / 0 | 95/95 (100%) | 0 / 0 |
| companies-v5 | education, retail, security_installer, coworking, car_servicing | 76/96 (79%) | 12 / 4 | 94/98 (96%) | 0 / 0 |
| companies-v6 | pest_control, bike_shop, agency, ferry_and_day_trips, care_home_group | 55/76 (72%) | 0 / 1 | 74/76 (97%) | 0 / 0 |
| companies-v7 | wholesale distributor (dental consumables), driving school, law firm (residential property, family, wills), furniture maker and showroom, children's day nursery (two sites: Elm Road, Canal Street) | 48/68 (71%) | 0 / 5 | 90/96 (94%) | 0 / 0 |
| companies-v8 | roofing_contractor, farm_shop, sign_maker, bookkeeping_saas_and_service, spa_hotel | 74/102 (73%) | 1 / 5 | 101/102 (99%) | 0 / 0 |
| companies-v9 | optician, dog groomer, wedding venue, IT training provider, EV charger installer | 55/76 (72%) | 0 / 6 | 98/100 (98%) | 0 / 0 |
| companies-v10 | B2B commercial printer, self storage (3 sites), language school, driving school, veterinary practice | 57/59 (97%) | 2 / 13 | 61/61 (100%) | 0 / 0 |
| companies-v11 | commercial and domestic cleaning company (B2B focus), e-bike shop (sales and workshop), HR and payroll SaaS (Essentials / Growth / Scale), car garage and MOT centre (Canal Street + Ashby Road), children's party entertainer (sole trader) | 56/60 (93%) | 0 / 8 | 63/63 (100%) | 0 / 0 |
| companies-v12 | community pharmacy (two branches), craft brewery with taproom and webshop, removals company (home and business moves), managed security provider (B2B, plan tiers), music school (group, one-to-one and parent & child lessons) | 47/65 (72%) | 1 / 8 | 67/69 (97%) | 0 / 0 |
| companies-v13 | B2B bike courier, florist (2 shops), saas (B2B CRM), caravan and holiday park, children's swim school | 26/33 (79%) | 0 / 4 | 39/47 (83%) | 0 / 3 |
| real-hotel-1 | hospitality, hospitality, hospitality, hospitality, hospitality | 52/70 (74%) | 6 / 4 | 69/70 (99%) | 0 / 0 |
| real-drinks-1 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B) | 47/62 (76%) | 1 / 2 | 58/62 (94%) | 0 / 2 |
| real-hotel-2 | hospitality, hospitality, hospitality, hospitality, hospitality | 53/72 (74%) | 3 / 1 | 70/72 (97%) | 0 / 0 |
| real-mixed-3 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 50/59 (85%) | 1 / 2 | 59/59 (100%) | 1 / 0 |
| real-4 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 65/81 (80%) | 1 / 4 | 80/81 (99%) | 1 / 0 |
| real-5 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 68/90 (76%) | 1 / 11 | 76/90 (84%) | 0 / 0 |
| real-6 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 84/103 (82%) | 0 / 0 | 102/103 (99%) | 0 / 0 |
| real-7 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 77/118 (65%) | 5 / 1 | 107/118 (91%) | 3 / 0 |
| real-8 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 105/162 (65%) | 0 / 0 | 136/162 (84%) | 0 / 0 |
| real-9 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 122/203 (60%) | 1 / 0 | 128/203 (63%) | 1 / 0 |
| real-10 | beverage distribution (B2B), beverage distribution (B2B), beverage distribution (B2B), hospitality, hospitality | 137/206 (67%) | 1 / 8 | 137/206 (67%) | 1 / 0 |
| **first-seen total** | | **1529/2074 (74%)** | **37 / 89** | | |
