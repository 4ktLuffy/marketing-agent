"""Zero-AI templates: rendered from the facts, then the same checks and approval as a pasted piece."""
import copy

from app import templates_render as T

from . import data
from .conftest import AUTH, BRAND, canon
from .data import fact

PUB = data.PUBLISH
OVER21 = "No sales to anyone under 21."
KIT = [{"kind": "required_disclosure", "phrase": OVER21, "why": "any alcohol advertisement", "status": "active"},
       {"kind": "required_disclosure", "phrase": "Draft only. Hold for human review.", "why": "note", "status": "active"},
       {"kind": "forbidden_phrase", "phrase": "official beer of", "why": "x", "status": "active"}]
SCOPE = {"sites": ["riverton"]}


def crate(key, ref, value, **kw):
    return fact(key, f"{ref}: {value:,} kora per crate.", f"{value:,} kora per crate", subject={"kind": "product", "ref": ref},
                fact_type="price", value=value, currency="XKR", unit="crate", required_disclosures=["per crate"],
                sites=["riverton"], **kw)


def beer():
    return [
        crate("p-brand-a", "Brand A 33cl", 2080),
        crate("p-brand-b", "Brand B 33cl", 1700),
        crate("p-brand-d", "Brand D 50cl", 1620),
        crate("p-old", "Brand A 33cl", 1980, valid_to="2026-08-16"),        # expired
        fact("p-internal", "Partner crate price", "1,111 kora per crate", subject={"kind": "product", "ref": "Brand C 33cl"},
             fact_type="price", value=1111, currency="XKR", sensitivity="internal", required_disclosures=["per crate"],
             sites=["riverton"]),
        fact("p-secret", "Cost", "999 kora per crate", subject={"kind": "product", "ref": "Sofi 33cl"},
             fact_type="price", value=999, currency="XKR", sensitivity="restricted", sites=["riverton"]),
        fact("p-otherSite", "Other site", "1,000 kora per crate", subject={"kind": "product", "ref": "Bale 33cl"},
             fact_type="price", value=1000, currency="XKR", sites=["easton"], required_disclosures=["per crate"]),
        fact("hours", "Open 8am to 6pm.", "8am to 6pm", subject={"kind": "site", "ref": "Riverton depot"},
             fact_type="hours", sites=["riverton"]),
    ]


def rooms():
    out = []
    for key, ref, v, n in (("tw-1", "Twin Room (Lake View)", 125, 1), ("tw-2", "Twin Room (Lake View)", 131, 2),
                           ("dbl-1", "Double Room (Garden)", 110, 1), ("dbl-2", "Double Room (Garden)", 118, 2)):
        out.append(fact(key, f"{ref} for {n}.", f"${v} per room per night, breakfast included",
                        subject={"kind": "room", "ref": ref}, fact_type="price", value=v, currency="USD",
                        conditions=[{"key": "guests", "op": "=", "value": n}],
                        required_disclosures=["per room per night", "breakfast included"]))
    return out


def render(client, stack, facts, **kw):
    stack.facts = facts
    body = {"template": "price_list", "channel": "telegram", "scope": SCOPE, "publish_on": PUB, **kw}
    r = client.post("/templates/render", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_price_list_public_only_and_disclosure_once(client, stack):
    out = render(client, stack, beer())
    t = out["text"]
    assert "2,080 kora" in t and "1,700 kora" in t and "1,620 kora" in t
    for bad in ("1,980", "1,111", "999", "1,000 kora", "Brand C", "Sofi", "Bale", "[[", "8am"):
        assert bad not in t, bad
    assert t.count("per crate") == 1 and "Prices per crate:" in t
    assert sorted(out["facts_used"]) == ["p-brand-a", "p-brand-b", "p-brand-d"]
    assert out["chars"] == len(t)
    # sorted by subject
    assert t.index("Brand A") < t.index("Brand B") < t.index("Brand D")


def test_kit_disclosure_only_when_rules_active(client, stack):
    stack.kit_rules = []
    assert OVER21 not in render(client, stack, beer())["text"]
    stack.kit_rules = KIT
    t = render(client, stack, beer())["text"]
    assert t.rstrip().endswith(OVER21) and "Hold for human review" not in t


def test_rate_card_groups_guests(client, stack):
    out = render(client, stack, rooms(), template="rate_card", channel="email", scope={})
    t = out["text"]
    assert "Twin Room (Lake View): $125 one guest / $131 two guests" in t
    assert "Double Room (Garden): $110 one guest / $118 two guests" in t
    assert "Per room per night, breakfast included:" in t
    assert t.count("per room per night") == 0 or t.lower().count("per room per night") == 1
    assert t.index("Double") < t.index("Twin")


def test_no_markdown_channel_uses_plain_bullets(client, stack):
    t = render(client, stack, beer(), channel="instagram", title="**Weekly** prices")["text"]
    assert "•" in t and "**" not in t and "\n- " not in t


def test_subject_filter_and_digest(client, stack):
    t = render(client, stack, beer(), subjects=["brand b"])["text"]
    assert "Brand B" in t and "Brand A" not in t
    d = render(client, stack, beer(), template="facts_digest")
    assert "Riverton depot: 8am to 6pm" in d["text"] and "p-internal" not in d["facts_used"]


def test_sms_compact_under_160(client, stack):
    stack.kit_rules = KIT
    out = render(client, stack, beer(), channel="sms")
    assert len(out["text"]) <= 160 and OVER21 in out["text"] and "Prices per crate:" in out["text"]
    assert "Price list" not in out["text"] and out["facts_used"]      # no title in a text message
    many = beer() + [crate(f"q-{i}", f"Brand {i} 33cl", 1500 + i) for i in range(10)]
    out = render(client, stack, many, channel="sms")
    assert len(out["text"]) <= 160 and OVER21 in out["text"]
    assert len(out["facts_used"]) < 13 and any("left out" in n for n in out["notes"])


def test_telegram_limit_splits_into_messages(client, stack):
    many = [crate(f"p-{i}", f"Brand {i:03d} 33cl", 1000 + i) for i in range(260)]
    stack.kit_rules = KIT
    out = render(client, stack, many)
    assert len(out["parts"]) >= 2 and all(len(p) <= 4096 for p in out["parts"])
    assert all("Prices per crate:" in p and OVER21 in p for p in out["parts"])
    assert len(out["facts_used"]) == 260 and any("split into" in n for n in out["notes"])


def test_empty_facts_say_so(client, stack):
    out = render(client, stack, [])
    assert out["text"] == "" and out["facts_used"] == [] and any("no price facts" in n for n in out["notes"])
    r = client.post("/templates/task", json={"template": "price_list", "channel": "telegram", "scope": SCOPE,
                                             "publish_on": PUB}, headers=AUTH)
    assert r.status_code == 422 and "nothing to render" in r.text


def test_task_path_submits_and_checker_is_clean(client, stack):
    stack.facts = beer()
    stack.kit_rules = KIT
    r = client.post("/templates/task", json={"template": "price_list", "channels": ["telegram", "linkedin"],
                                             "scope": SCOPE, "publish_on": PUB}, headers=AUTH)
    assert r.status_code == 201, r.text
    d = r.json()
    assert len(d["pieces"]) == 2 and d["blocked"] is False, d
    for p in d["pieces"]:
        assert not [f for f in p["findings"] if f.get("blocking")], p["findings"]
        assert p["status"] == "in_review" and p["calendar_item_id"]
        item = stack.items[p["calendar_item_id"]]
        assert item["body_sha256"] == canon(p["filled_text"]) and item["require_bound"] is True
    assert stack.items[1]["origin"] == "task-bridge"
    assert any(x["provider"] == "template" for x in [{"provider": "template"}])
    # the same text through the stateless checker
    t = d["pieces"][0]["filled_text"]
    c = client.post("/check", json={"text": t, "scope": SCOPE, "publish_on": PUB, "channel": "telegram"}, headers=AUTH).json()
    assert c["blocked"] is False


def test_rate_card_and_digest_pass_own_checker(client, stack):
    stack.facts = rooms()
    r = client.post("/templates/task", json={"template": "rate_card", "channel": "email", "scope": {},
                                             "publish_on": PUB, "title": "Our room rates"}, headers=AUTH)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["blocked"] is False, d["pieces"][0]["findings"]
    stack.facts = beer()
    r = client.post("/templates/task", json={"template": "facts_digest", "channel": "telegram", "scope": SCOPE,
                                             "publish_on": PUB}, headers=AUTH)
    assert r.status_code == 201 and r.json()["blocked"] is False, r.text


def test_forbidden_phrase_in_title_is_warned(client, stack):
    stack.kit_rules = KIT
    out = render(client, stack, beer(), title="The official beer of Riverton")
    assert any("official beer of" in n for n in out["notes"])


def test_unit_helpers():
    heads, tail = T._split_heads(["$125 per room per night", "$131 per room per night"])
    assert heads == ["$125", "$131"] and tail == "per room per night"
    assert T._strip("$125 per room per night, breakfast included", ["per room per night", "breakfast included"]) == "$125"
