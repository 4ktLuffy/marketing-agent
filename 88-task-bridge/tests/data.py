"""Unit-test businesses (invented for these tests; not the held-out eval companies).

Today is 2026-09-29, copy publishes 2026-10-06. Each business has a scope trap and a fact that is
valid today but expired at the publish date. Sentinel strings mark values that must never leave:
INTERNAL_SENTINEL (internal, may only travel as a slot) and RESTRICTED_SENTINEL (never at all).
"""
import copy

TODAY = "2026-09-29"
PUBLISH = "2026-10-06"
INTERNAL_SENTINEL = "£13.37 partner rate"
RESTRICTED_SENTINEL = "£9.91 supplier cost"


def fact(key, text, value_text=None, **kw):
    f = {
        "key": key, "subject": kw.pop("subject", {"kind": "business", "ref": ""}),
        "fact_type": kw.pop("fact_type", "claim"), "attribute": kw.pop("attribute", None),
        "value": kw.pop("value", None), "unit": kw.pop("unit", None), "currency": kw.pop("currency", None),
        "value_text": value_text, "basis": kw.pop("basis", None), "conditions": kw.pop("conditions", []),
        "scope": {"sites": [], "regions": [], "channels": [], "segments": [], "plan_tiers": [], "variants": []},
        "valid_from": kw.pop("valid_from", None), "valid_to": kw.pop("valid_to", None),
        "review_by": kw.pop("review_by", None), "source": {"kind": "doc", "ref": "test sheet"},
        "claim_class": kw.pop("claim_class", "none"), "requires_evidence": False, "evidence_ref": None,
        "consent_ref": None, "required_disclosures": kw.pop("required_disclosures", []),
        "allowed_phrasing": kw.pop("allowed_phrasing", []), "forbidden_phrasing": kw.pop("forbidden_phrasing", []),
        "owner": "Owner", "risk": "low", "sensitivity": kw.pop("sensitivity", "public"),
        "status": kw.pop("status", "active"), "supersedes_key": None, "text": text, "version": kw.pop("version", 1),
    }
    for dim in list(kw):
        if dim in f["scope"]:
            f["scope"][dim] = kw.pop(dim)
    assert not kw, kw
    return f


# ---------- Tidewater Bike Hire: two sites, a summer offer that ends before publish day

BIKES = [
    fact("day-hire-porthleven", "A hybrid bike for a day at Porthleven costs £28.", "£28 per day",
         subject={"kind": "service", "ref": "Hybrid bike day hire"}, fact_type="price", attribute="day rate",
         value=28, currency="GBP", sites=["porthleven"]),
    fact("day-hire-falmouth", "A hybrid bike for a day at Falmouth costs £32.", "£32 per day",
         subject={"kind": "service", "ref": "Hybrid bike day hire"}, fact_type="price", attribute="day rate",
         value=32, currency="GBP", sites=["falmouth"]),
    fact("ebike-day", "E-bike day hire costs £45 at both shops.", "£45 per day",
         subject={"kind": "service", "ref": "E-bike day hire"}, fact_type="price", attribute="day rate",
         value=45, currency="GBP", required_disclosures=["riders must be 16 or over"]),
    fact("summer-saver", "Summer Saver: 20% off weekday hires until 30 September.", "20% off weekday hires",
         subject={"kind": "offer", "ref": "Summer Saver"}, fact_type="price", attribute="discount",
         value=20, unit="%", valid_from="2026-06-01", valid_to="2026-09-30"),
    fact("insurance", "Every hire includes third-party insurance and a helmet.", "third-party insurance and a helmet",
         subject={"kind": "policy", "ref": "Hire terms"}, fact_type="inclusion"),
    fact("partner-rate", "Partner hotels get a special rate.", INTERNAL_SENTINEL,
         subject={"kind": "offer", "ref": "Hotel partners"}, fact_type="price", attribute="partner rate",
         value=13.37, currency="GBP", sensitivity="internal"),
    fact("supplier-cost", "What a hybrid bike costs us.", RESTRICTED_SENTINEL,
         subject={"kind": "product", "ref": "Hybrid bike"}, fact_type="price", attribute="cost",
         value=9.91, currency="GBP", sensitivity="restricted"),
    fact("no-cheapest", "We never claim to be the cheapest.", None, subject={"kind": "policy", "ref": "Claims"},
         fact_type="policy", forbidden_phrasing=[{"phrase": "cheapest in Cornwall", "why": "no price survey"}]),
]

# ---------- Quillstack: scheduling SaaS, SSO only on Enterprise, ISO 27001 but no SOC 2

SAAS = [
    fact("pro-price", "Quillstack Pro costs £12 per user per month, billed annually.", "£12 per user per month",
         subject={"kind": "plan", "ref": "Pro plan"}, fact_type="price", attribute="price",
         value=12, currency="GBP", required_disclosures=["billed annually"], plan_tiers=["pro"]),
    fact("sso", "Single sign-on (SSO) is included on the Enterprise plan.", "single sign-on",
         subject={"kind": "plan", "ref": "Enterprise plan"}, fact_type="spec", attribute="sso",
         plan_tiers=["enterprise"], allowed_phrasing=["single sign-on"], claim_class="security"),
    fact("iso", "Quillstack is ISO 27001 certified (certificate QS-2291).", "ISO 27001 certified",
         subject={"kind": "business", "ref": "Quillstack"}, fact_type="certification", claim_class="security",
         allowed_phrasing=["ISO 27001 certified"],
         forbidden_phrasing=[{"phrase": "SOC 2", "why": "no SOC 2 report"}]),
    fact("uptime", "Quillstack guarantees 99.9% monthly uptime in its SLA.", "99.9% monthly uptime",
         subject={"kind": "business", "ref": "Quillstack"}, fact_type="claim", claim_class="security",
         value=99.9, unit="%", required_disclosures=["see our SLA"]),
    fact("trial", "Every plan has a 14-day free trial.", "14-day free trial",
         subject={"kind": "business", "ref": "Quillstack"}, fact_type="policy", value=14, unit="days"),
    fact("calendar-beta", "Google Calendar two-way sync is in free beta.", "free beta",
         subject={"kind": "product", "ref": "Google Calendar sync"}, fact_type="availability",
         status="expired", valid_to="2026-08-31"),
]

# ---------- Greenbank Bakery: award for one loaf only, free delivery only in Truro

BAKERY = [
    fact("sourdough-price", "A Greenbank Sourdough loaf costs £4.50.", "£4.50 a loaf",
         subject={"kind": "product", "ref": "Greenbank Sourdough"}, fact_type="price", value=4.5, currency="GBP"),
    fact("sourdough-award", "Greenbank Sourdough won Gold at the 2025 Cornwall Baking Awards.", "Gold, 2025 Cornwall Baking Awards",
         subject={"kind": "product", "ref": "Greenbank Sourdough"}, fact_type="credential", claim_class="comparative",
         allowed_phrasing=["award-winning"]),
    fact("no-award-generic", "Only the sourdough has an award.", None, subject={"kind": "policy", "ref": "Claims"},
         fact_type="policy", forbidden_phrasing=["award-winning"]),
    fact("free-delivery-truro", "Free delivery on orders over £25 within Truro.", "free delivery on orders over £25",
         subject={"kind": "policy", "ref": "Delivery"}, fact_type="policy", value=25, currency="GBP",
         regions=["truro"], required_disclosures=["within Truro"]),
    fact("gluten-rule", "The kitchen handles wheat.", None, subject={"kind": "policy", "ref": "Allergens"},
         fact_type="policy", forbidden_phrasing=[{"phrase": "gluten-free", "why": "shared kitchen"}]),
    fact("hours", "The bakery is open 7am to 3pm, Tuesday to Saturday.", "7am to 3pm",
         subject={"kind": "site", "ref": "Greenbank bakery"}, fact_type="hours"),
    fact("harvest-box", "The Harvest Box costs £18 until 30 September.", "£18",
         subject={"kind": "offer", "ref": "Harvest Box"}, fact_type="price", value=18, currency="GBP",
         valid_to="2026-09-30"),
]


def business(name):
    return copy.deepcopy({"bikes": BIKES, "saas": SAAS, "bakery": BAKERY}[name])
