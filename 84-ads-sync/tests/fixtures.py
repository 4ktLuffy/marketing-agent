"""Fixed API answers in the documented shapes. No real Meta or Google call is ever made.

Meta Insights (GET /act_<id>/insights, time_increment=1): {"data": [{date_start, date_stop,
campaign_id, campaign_name, account_currency, spend (string), impressions, clicks, actions:
[{action_type, value}], action_values: [...]}], "paging": {"cursors": {...}, "next": url}}.
Google Ads searchStream: a JSON ARRAY of batches {"results": [{"customer": {"currencyCode"},
"campaign": {"resourceName", "id", "name", "finalUrlSuffix"}, "segments": {"date"}, "metrics":
{"costMicros": "40000000", "impressions": "500", "clicks": "30", "conversions": 2.0,
"conversionsValue": 150.0}}], "fieldMask": "...", "requestId": "..."}; int64 are strings.

Story (September 2026, as of the 27th): "autumn-launch" runs on Meta (named "Autumn Launch |
Prospecting") and Google ("Brand Search", utm_campaign=autumn-launch in its URL suffix). With a
3,000 EUR monthly budget it is 23% ahead of plan. Meta "Retargeting - Q3" spent 3 days with 0 leads.
"""
from datetime import date, timedelta

TODAY = date(2026, 9, 28)
AS_OF = date(2026, 9, 27)
META_ACCOUNT = "act_111222333"
GOOGLE_CUSTOMER = "1234567890"


def days(start=date(2026, 9, 1), end=AS_OF):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def meta_row(d, cid, name, spend, imp, clicks, leads, revenue=0.0):
    row = {"date_start": d.isoformat(), "date_stop": d.isoformat(), "account_currency": "EUR",
           "campaign_id": cid, "campaign_name": name, "spend": f"{spend:.2f}", "impressions": str(imp),
           "clicks": str(clicks)}
    if leads:
        # "lead" and the pixel lead overlap; only META_CONVERSION_ACTIONS (default "lead") counts.
        row["actions"] = [{"action_type": "lead", "value": str(leads)},
                          {"action_type": "offsite_conversion.fb_pixel_lead", "value": str(leads)},
                          {"action_type": "link_click", "value": str(clicks)}]
    if revenue:
        row["action_values"] = [{"action_type": "omni_purchase", "value": f"{revenue:.2f}"}]
    return row


def meta_rows(start=date(2026, 9, 1), end=AS_OF):
    rows = []
    for d in days(start, end):
        last_week = d >= date(2026, 9, 21)
        rows.append(meta_row(d, "23850001", "Autumn Launch | Prospecting", 90 if last_week else 80, 4000,
                             60, 3 if last_week else 4, 200.0))
        rows.append(meta_row(d, "23850002", "Retargeting - Q3", 20, 1000, 10, 0 if d >= date(2026, 9, 25) else 1))
    return rows


def meta_pages(start=date(2026, 9, 1), end=AS_OF, version="v26.0"):
    rows = meta_rows(start, end)
    half = len(rows) // 2
    nxt = (f"https://graph.facebook.com/{version}/{META_ACCOUNT}/insights?level=campaign&limit=500"
           "&time_increment=1&after=QVFIUpage2")
    return [{"data": rows[:half], "paging": {"cursors": {"before": "a", "after": "QVFIUpage2"}, "next": nxt}},
            {"data": rows[half:], "paging": {"cursors": {"before": "b", "after": "c"}}}]


def google_stream(start=date(2026, 9, 1), end=AS_OF):
    results = []
    for d in days(start, end):
        results.append({
            "customer": {"resourceName": f"customers/{GOOGLE_CUSTOMER}", "currencyCode": "EUR"},
            "campaign": {"resourceName": f"customers/{GOOGLE_CUSTOMER}/campaigns/9001", "id": "9001",
                         "name": "Brand Search",
                         "finalUrlSuffix": "utm_source=google&utm_medium=cpc&utm_campaign=autumn-launch"},
            "segments": {"date": d.isoformat()},
            "metrics": {"costMicros": "40000000", "impressions": "500", "clicks": "30",
                        "conversions": 2.0, "conversionsValue": 150.0},
        })
    half = len(results) // 2
    mask = "customer.currencyCode,campaign.id,campaign.name,segments.date,metrics.costMicros"
    return [{"results": results[:half], "fieldMask": mask, "requestId": "req1"},
            {"results": results[half:], "fieldMask": mask, "requestId": "req1"}]


CAMPAIGNS_45 = [{"id": 1, "slug": "autumn-launch", "name": "Autumn Launch", "status": "active"},
                {"id": 2, "slug": "spring-sale", "name": "Spring Sale", "status": "done"}]

GOOGLE_TOKEN = {"access_token": "ya29.test-access-token-value", "expires_in": 3599, "token_type": "Bearer"}
