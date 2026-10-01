"""Relevance, segments, compact rate lines and the target size, on a realistic 90+ fact hotel."""
import re

from app import pack

from . import data
from .data import fact

GOAL = "Fill lake-view rooms in Arba Minch for weekends in October"
AUDIENCE = "couples and families in Addis Ababa, foreign travellers"
PIECES = [{"key": f"p{i}", "channel": c, "kind": "post"} for i, c in enumerate(("facebook", "instagram", "telegram"), 1)]
ROOMS = [("Twin Room", "Lake View", 125), ("Twin Room", "Garden View", 95), ("Double Room", "Lake View", 140),
         ("Double Room", "Garden View", 105), ("Family Room", "Lake View", 190), ("Family Room", "Garden View", 150),
         ("Suite", "Lake View", 260), ("Suite", "Garden View", 210), ("Standard Room", "Garden View", 80)]
TOUR = "tour operators"


def rate(key, room, view, guests, period, amount, internal=False, segment=None):
    conds = [{"key": "guests", "op": "=", "value": guests}, {"key": "period", "op": "=", "value": period}]
    if segment:
        conds.append({"key": "segment", "op": "=", "value": segment})
    secret = f"INTERNAL-{amount}-tourrate"
    return fact(key, f"The {room} ({view}) costs ${amount} per room per night for {guests} guest(s), {period}.",
                secret if internal else f"${amount} per room per night",
                subject={"kind": "service", "ref": f"{room} ({view})"}, fact_type="price", attribute="room rate",
                value=amount, currency="USD", basis="per_room", conditions=conds,
                sensitivity="internal" if internal else "public",
                required_disclosures=["breakfast is not included"] if not internal else [])


def hotel():
    fs = []
    for room, view, base in ROOMS:
        slug = re.sub(r"[^a-z]+", "-", f"{room}-{view}".lower()).strip("-")
        for period, bump in (("weekend", 10), ("weekday", 0)):
            for guests, extra in ((1, 0), (2, 6)):
                n = base + bump + extra
                fs.append(rate(f"rate-{slug}-{period}-g{guests}", room, view, guests, period, n))
                fs.append(rate(f"tour-{slug}-{period}-g{guests}", room, view, guests, period, n - 30,
                               internal=True, segment=TOUR))
    fs += [fact("site-hotel", "Lakeside Lodge is a 48-room hotel on the shore of Lake Chamo in Arba Minch.",
                "48-room lakeshore hotel", subject={"kind": "business", "ref": "Lakeside Lodge"}, fact_type="identity"),
           fact("contact-phone", "Reservations: +251 46 881 0000.", "+251 46 881 0000",
                subject={"kind": "contact", "ref": "Reservations"}, fact_type="contact"),
           fact("contact-telegram", "Book on Telegram @lakesidelodge.", "@lakesidelodge",
                subject={"kind": "contact", "ref": "Telegram booking"}, fact_type="contact"),
           fact("restaurant", "The lakeside restaurant serves local fish and is open daily.", "open daily",
                subject={"kind": "service", "ref": "Lakeside restaurant"}, fact_type="service"),
           fact("spa", "The spa offers massages for guests.", "massages", subject={"kind": "service", "ref": "Spa"},
                fact_type="service")]
    for i, (name, hrs) in enumerate([("Crocodile Market boat trip", "2 hours"), ("Nechisar National Park",
                                                                               "half day"), ("Dorze village visit", "full day"),
                                     ("Forty Springs walk", "2 hours"), ("Lake Chamo sunset cruise", "1 hour"),
                                     ("Bridge of God viewpoint", "1 hour"), ("Hot springs visit", "half day"),
                                     ("Coffee ceremony", "1 hour")]):
        fs.append(fact(f"exc-{i}", f"{name} takes {hrs} and starts from the hotel.", hrs,
                       subject={"kind": "service", "ref": name}, fact_type="service"))
    return fs


KEY = ["rate-twin-room-lake-view-weekend-g1", "rate-twin-room-lake-view-weekend-g2", "site-hotel", "contact-phone",
       "contact-telegram"]


def run(facts, goal=GOAL, audience=AUDIENCE, rules=None, builder=pack.build, **kw):
    return builder("T-TEST01", "fs-1-abcdef12", data.PUBLISH, goal, audience, None, PIECES, {}, facts, [], facts, None,
                   rules, 8000, **kw)


def lines(built):
    return [x for x in built.text.split("\n") if x.startswith("- ")]


def test_fixture_is_realistic():
    fs = hotel()
    assert len(fs) >= 80
    assert sum(1 for f in fs if f["sensitivity"] == "internal") >= 30


def test_pack_is_short_relevant_and_keeps_the_key_facts():
    b = run(hotel(), target_chars=5000)
    assert len(b.text) <= 5000
    keys = {s["key"] for s in b.snapshot}
    for k in KEY:
        assert k in keys, k
    assert not [k for k in keys if k.startswith("tour-")]                    # internal, other segment
    assert not [k for k in keys if "garden" in k]                            # the goal says lake view
    w = {x["key"]: x["reason"] for x in b.withheld}
    assert w["tour-twin-room-lake-view-weekend-g1"] == "internal_other_segment"
    assert w["rate-twin-room-garden-view-weekend-g1"] == "not_relevant"
    assert "too_long" not in w.values() or all(w[k] != "too_long" for k in KEY)


def test_rates_of_one_subject_are_one_line_with_the_same_slots():
    b = run(hotel(), target_chars=5000)
    twin = [x for x in lines(b) if x.startswith("- Twin Room (Lake View)")]
    assert len(twin) == 2                                                    # weekend and weekday
    wk = [x for x in twin if "weekend" in x][0]
    assert wk.count("[[") == 2
    assert "= $135 one guest / [[rate-twin-room-lake-view-weekend-g2]] = $141 two guests, per room per night" in wk
    assert '(must include: "breakfast is not included")' in wk
    assert wk in b.sent
    snap = {s["key"] for s in b.snapshot}
    assert {"rate-twin-room-lake-view-weekend-g1", "rate-twin-room-lake-view-weekend-g2"} <= snap
    assert len(b.snapshot) == len(b.snapshot_facts)


def test_internal_segment_rates_are_kept_for_a_tour_operator_task():
    b = run(hotel(), goal="Lake-view room rates for tour operators, weekends in October",
            audience="tour operators and travel agents")
    assert b.slotted and all(k.startswith("tour-") for k in b.slotted)
    assert "INTERNAL-" not in b.text
    assert any("tour-twin-room-lake-view-weekend-g1" in x for x in lines(b))


def test_variants_not_asked_for_stay_when_the_task_names_none():
    b = run(hotel(), goal="Weekend stays at the hotel in Arba Minch")
    assert any("garden" in s["key"] for s in b.snapshot)


def test_target_chars_default_and_hard_cap():
    big = run(hotel(), goal="Weekend stays at the hotel in Arba Minch", target_chars=3000)
    assert len(big.text) <= 3000
    b = run(hotel(), goal="Weekend stays at the hotel in Arba Minch", target_chars=100_000)
    assert len(b.text) <= 8000


def test_before_after_sizes(capsys):
    """Prints the measurement used in the change notes."""
    import importlib.util
    import pathlib
    old_path = pathlib.Path("/private/tmp/claude-502/-Users-Twinkle-AI-Engineering/ffd6668b-7670-4925-8621-23251d66f697/"
                            "scratchpad/old/pack_old.py")
    if not old_path.exists():
        return
    spec = importlib.util.spec_from_file_location("app.pack_old", old_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fs = hotel()
    old = run(fs, builder=mod.build)
    new = run(fs, target_chars=5000)
    def cov(b):
        keys = {s["key"] for s in b.snapshot}
        return [k for k in KEY if k in keys], len(b.snapshot), len(b.withheld), sum(1 for k in keys if k.startswith("tour-")), \
            sum(1 for k in keys if "garden" in k)
    with capsys.disabled():
        print(f"\nOLD chars={len(old.text)} lines={len(lines(old))} (key facts, facts, withheld, tour, garden)={cov(old)}")
        print(f"NEW chars={len(new.text)} lines={len(lines(new))} {cov(new)}")


def test_telegram_channel_rules_in_the_pack_and_aliases(client, stack):
    from app import channels
    from .conftest import BIKE_TASK, make_task
    assert channels.canonical("Telegram channel post") == "telegram" and channels.canonical("tg") == "telegram"
    assert not channels.no_markdown("telegram")
    t = make_task(client, stack, task={**BIKE_TASK, "pieces": [{"key": "p1", "channel": "telegram", "kind": "post"}]})
    assert "=== 1 TELEGRAM ===   (post, max 4096 characters)" in t["pack"]
    assert "CHANNEL RULES:" in t["pack"] and "- telegram: max 4096 characters; 1024 as a photo caption" in t["pack"]
    assert "no markdown" not in t["pack"]
