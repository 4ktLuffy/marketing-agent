"""CRM connectors: create-or-update a contact by email, then add a note.

HubSpot (CRM v3, private app token):
  GET   /crm/v3/objects/contacts/{email}?idProperty=email   -> 200 {id} or 404
  POST  /crm/v3/objects/contacts            {"properties": {...}}            (create)
  PATCH /crm/v3/objects/contacts/{id}       {"properties": {...}}            (update)
  POST  /crm/v3/objects/notes  {"properties": {hs_timestamp, hs_note_body},
        "associations": [{"to": {"id"}, "types": [{HUBSPOT_DEFINED, 202}]}]}  (202 = note->contact)
Pipedrive (API token in header x-api-token, base https://<company>.pipedrive.com):
  GET   /api/v2/persons/search?term=<email>&fields=email&exact_match=true
  POST  /api/v2/persons      {"name", "emails": [{"value","primary","label"}]}
  PATCH /api/v2/persons/{id} {"name"}
  POST  /api/v1/notes        {"content", "person_id"}

With DRY_RUN (the default) nothing is sent: the planned requests are returned instead,
with the token left out. Tokens never appear in results, errors or logs.
"""
import html
import os
from urllib.parse import quote

import httpx

TIMEOUT = 20


def note_html(note: str) -> str:
    """Both CRMs render notes as HTML. The note carries text the lead typed (message, name) and
    text from their website, so it is escaped: no tracking pixels or disguised links in the
    team's CRM (security review 2026-09-28, LH-2)."""
    return html.escape(note, quote=False).replace("\n", "<br>")


class CRMError(Exception):
    """Safe to store: status codes and step names only, never tokens or response bodies."""


def dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").strip().lower() not in ("false", "0", "no")


def configured() -> list[str]:
    out = []
    if os.environ.get("HUBSPOT_TOKEN"):
        out.append("hubspot")
    if os.environ.get("PIPEDRIVE_TOKEN") and os.environ.get("PIPEDRIVE_BASE_URL"):
        out.append("pipedrive")
    wanted = [c.strip().lower() for c in os.environ.get("CRM", "").split(",") if c.strip()]
    if wanted:
        # CRM names the targets; in a dry run they are shown even without a token.
        return [c for c in wanted if c in ("hubspot", "pipedrive") and (c in out or dry_run())]
    return out


def split_name(name: str | None) -> tuple[str, str]:
    parts = (name or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


# ---------- HubSpot


def hubspot_base() -> str:
    return os.environ.get("HUBSPOT_BASE_URL", "https://api.hubapi.com").rstrip("/")


def hubspot_plan(lead: dict, note: str, tag: str) -> dict:
    first, last = split_name(lead.get("name"))
    props = {"email": lead["email"]}
    if first:
        props["firstname"] = first
    if last:
        props["lastname"] = last
    if lead.get("company_domain"):
        props["website"] = f"https://{lead['company_domain']}"
    tag_prop = os.environ.get("HUBSPOT_TAG_PROPERTY", "").strip()
    if tag_prop:
        props[tag_prop] = tag
    create = dict(props)
    create["lifecyclestage"] = "lead"  # only on create: HubSpot does not move stages backwards
    b = hubspot_base()
    return {
        "crm": "hubspot",
        "lookup": {"method": "GET", "url": f"{b}/crm/v3/objects/contacts/{quote(lead['email'], safe='@')}",
                   "params": {"idProperty": "email"}},
        "create": {"method": "POST", "url": f"{b}/crm/v3/objects/contacts", "json": {"properties": create}},
        "update": {"method": "PATCH", "url": f"{b}/crm/v3/objects/contacts/<id>", "json": {"properties": props}},
        "note": {"method": "POST", "url": f"{b}/crm/v3/objects/notes", "json": {
            "properties": {"hs_timestamp": lead["now"], "hs_note_body": note_html(note)},
            "associations": [{"to": {"id": "<id>"}, "types": [
                {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}]}},
    }


def _check(r: httpx.Response, step: str, ok=(200, 201)) -> None:
    if r.status_code not in ok:
        raise CRMError(f"{step}: HTTP {r.status_code}")


def hubspot_push(plan: dict) -> dict:
    headers = {"Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}", "Content-Type": "application/json"}
    with httpx.Client(timeout=TIMEOUT, headers=headers, trust_env=False) as c:
        r = c.get(plan["lookup"]["url"], params=plan["lookup"]["params"])
        if r.status_code == 404:
            r = c.post(plan["create"]["url"], json=plan["create"]["json"])
            _check(r, "hubspot create contact")
            action = "created"
        else:
            _check(r, "hubspot lookup contact", ok=(200,))
            cid = str(r.json()["id"])
            r = c.patch(plan["update"]["url"].replace("<id>", cid), json=plan["update"]["json"])
            _check(r, "hubspot update contact", ok=(200,))
            action = "updated"
        cid = str(r.json()["id"])
        note = plan["note"]["json"]
        note["associations"][0]["to"]["id"] = cid
        r = c.post(plan["note"]["url"], json=note)
        _check(r, "hubspot create note")
        return {"crm": "hubspot", "action": action, "contact_id": cid, "note_id": str(r.json().get("id"))}


# ---------- Pipedrive


def pipedrive_base() -> str:
    return os.environ.get("PIPEDRIVE_BASE_URL", "https://<company>.pipedrive.com").rstrip("/")


def pipedrive_plan(lead: dict, note: str, tag: str) -> dict:
    b = pipedrive_base()
    name = (lead.get("name") or "").strip() or lead["email"]
    person = {"name": name, "emails": [{"value": lead["email"], "primary": True, "label": "work"}]}
    labels = {"nurture": os.environ.get("PIPEDRIVE_NURTURE_LABEL_ID", "").strip()}
    if labels.get(tag, "").isdigit():
        person["label_ids"] = [int(labels[tag])]
    update = {k: v for k, v in person.items() if k != "emails"}  # never overwrite their email list
    return {
        "crm": "pipedrive",
        "lookup": {"method": "GET", "url": f"{b}/api/v2/persons/search",
                   "params": {"term": lead["email"], "fields": "email", "exact_match": "true"}},
        "create": {"method": "POST", "url": f"{b}/api/v2/persons", "json": person},
        "update": {"method": "PATCH", "url": f"{b}/api/v2/persons/<id>", "json": update},
        "note": {"method": "POST", "url": f"{b}/api/v1/notes",
                 "json": {"content": note_html(note), "person_id": "<id>"}},
    }


def pipedrive_push(plan: dict) -> dict:
    headers = {"x-api-token": os.environ["PIPEDRIVE_TOKEN"], "Content-Type": "application/json"}
    with httpx.Client(timeout=TIMEOUT, headers=headers, trust_env=False) as c:
        r = c.get(plan["lookup"]["url"], params=plan["lookup"]["params"])
        _check(r, "pipedrive search person", ok=(200,))
        items = ((r.json().get("data") or {}).get("items")) or []
        if items:
            pid = int(items[0]["item"]["id"])
            r = c.patch(plan["update"]["url"].replace("<id>", str(pid)), json=plan["update"]["json"])
            _check(r, "pipedrive update person", ok=(200,))
            action = "updated"
        else:
            r = c.post(plan["create"]["url"], json=plan["create"]["json"])
            _check(r, "pipedrive create person")
            pid = int(r.json()["data"]["id"])
            action = "created"
        note = dict(plan["note"]["json"], person_id=pid)
        r = c.post(plan["note"]["url"], json=note)
        _check(r, "pipedrive create note")
        return {"crm": "pipedrive", "action": action, "person_id": pid,
                "note_id": (r.json().get("data") or {}).get("id")}


PLANS = {"hubspot": hubspot_plan, "pipedrive": pipedrive_plan}
PUSHES = {"hubspot": hubspot_push, "pipedrive": pipedrive_push}


def sync(lead: dict, note: str, tag: str) -> list[dict]:
    """Create-or-update the lead in every configured CRM. Dry run: plans only."""
    results = []
    for name in configured():
        plan = PLANS[name](lead, note, tag)
        if dry_run():
            results.append({"crm": name, "dry_run": True, "plan": plan})
            continue
        try:
            results.append({"dry_run": False, **PUSHES[name](plan)})
        except CRMError as exc:
            results.append({"crm": name, "dry_run": False, "error": str(exc)})
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            results.append({"crm": name, "dry_run": False, "error": f"{name}: {type(exc).__name__}"})
    return results
