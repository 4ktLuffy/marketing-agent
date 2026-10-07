"""A fake Odoo (real XML-RPC over a local socket, in a thread) and a fake 05 (respx).

Every Odoo call is logged so tests can prove which methods and fields were used. Any 05 route that
confirms, retires or imports is mocked only so tests can assert nobody called it.
"""
import copy
import json
import threading
from pathlib import Path
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

import httpx
import pytest
import respx

from app import config
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = str(ROOT / "config" / "mappings.example.yaml")
KEY = "internal-api-key-1"
BRAND = "http://brand.test"
ODOO_KEY = "SENTINEL-ODOO-KEY-9f3a"
ODOO_LOGIN = "SENTINEL-LOGIN@example.com"
ODOO_DB = "SENTINEL-DB"

SERVICES = [
    {"id": 10, "name": "Starter Plan", "type": "service", "default_code": "STR-01", "list_price": 180.0,
     "standard_price": 120.0, "currency_id": [2, "GBP"], "company_id": [1, "Example Services Ltd"],
     "write_date": "2026-09-30 08:00:00", "partner_id": [99, "SENTINEL-PERSON"]},
    {"id": 11, "name": "Basic Plan", "type": "service", "default_code": "BAS-01", "list_price": 1.0,
     "standard_price": False, "currency_id": [2, "GBP"], "company_id": [1, "Example Services Ltd"],
     "write_date": "2026-09-29 08:00:00"},
    {"id": 13, "name": "Trial Plan", "type": "service", "default_code": "TRI-01", "list_price": 100.0,
     "standard_price": 0.0, "currency_id": [2, "GBP"], "company_id": [1, "Example Services Ltd"],
     "write_date": "2026-09-29 08:00:00"},
    {"id": 12, "name": "Hardware Kit", "type": "consu", "default_code": "HW-01", "list_price": 999.0,
     "standard_price": 999.0, "currency_id": [2, "GBP"], "company_id": [1, "Example Services Ltd"],
     "write_date": "2026-09-29 08:00:00"},
]
DRINKS = [
    {"id": 20, "name": "Cola 33cl", "company_id": 3, "sale_ok": True, "list_price": 12.0,
     "write_date": "2026-09-28 10:00:00"},
    {"id": 21, "name": "Lemonade 24x33cl", "company_id": 3, "sale_ok": True, "list_price": 21.0,
     "write_date": "2026-09-28 10:00:00"},
    {"id": 22, "name": "Tonic 20x50cl", "company_id": 3, "sale_ok": True, "list_price": 1.0,
     "write_date": "2026-09-28 10:00:00"},
    {"id": 23, "name": "Other company drink", "company_id": 2, "sale_ok": True, "list_price": 5.0,
     "write_date": "2026-09-28 10:00:00"},
    {"id": 24, "name": "Not for sale", "company_id": 3, "sale_ok": False, "list_price": 5.0,
     "write_date": "2026-09-28 10:00:00"},
]


class FakeOdoo:
    def __init__(self):
        self.calls: list[dict] = []
        self.records = {"product.template": []}
        self.reset()
        handler = type("H", (SimpleXMLRPCRequestHandler,), {"rpc_paths": ("/xmlrpc/2/common", "/xmlrpc/2/object")})
        self.server = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=handler, allow_none=True, logRequests=False)
        self.server.register_function(self._version, "version")
        self.server.register_function(self._auth, "authenticate")
        self.server.register_function(self._execute_kw, "execute_kw")
        # anything else a client tried to call would land here and be logged
        self.server.register_function(lambda *a: self._log("UNKNOWN", a), "write")
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reset(self):
        self.calls.clear()
        self.records["product.template"] = copy.deepcopy(SERVICES + DRINKS)

    def _log(self, name, args):
        self.calls.append({"method": name})
        return False

    def _version(self):
        self.calls.append({"method": "version"})
        return {"server_version": "17.0"}

    def _auth(self, db, login, key, ctx):
        self.calls.append({"method": "authenticate"})
        return 7 if (db, login, key) == (ODOO_DB, ODOO_LOGIN, ODOO_KEY) else False

    def _execute_kw(self, db, uid, key, model, method, args, kwargs):
        self.calls.append({"method": method, "model": model, "fields": kwargs.get("fields"), "args": args})
        if (db, uid, key) != (ODOO_DB, 7, ODOO_KEY):
            raise Exception("access denied")
        if method != "search_read":
            return []
        out = []
        for rec in self.records.get(model, []):
            if all(rec.get(t[0]) == t[2] for t in args[0] if isinstance(t, list)):
                row = {"id": rec["id"]}
                row.update({f: rec[f] for f in kwargs.get("fields", []) if f in rec})
                out.append(row)
        return out


@pytest.fixture(scope="session")
def _odoo():
    return FakeOdoo()


@pytest.fixture
def odoo(_odoo):
    _odoo.reset()
    return _odoo


def fact05(key, value, currency, status="active", version=1, latest=None, text="x"):
    return {"key": key, "value": value, "currency": currency, "status": status, "version": version,
            "latest_version": latest or version, "text": text}


class Fake05:
    def __init__(self):
        self.facts = {"price-starter-plan-list": fact05("price-starter-plan-list", 150, "GBP"),
                      "price-cola-33cl-crate": fact05("price-cola-33cl-crate", 12, "GBP")}
        self.writes: list[tuple[str, str, dict]] = []
        self.bad: list[str] = []
        self.versions: dict[str, list] = {}
        self.down = False
        self.post_status = 200


@pytest.fixture
def b05():
    f = Fake05()
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as m:
        def guard(fn):
            def inner(request, **kw):
                if request.headers.get("x-api-key") != KEY:
                    return httpx.Response(401, json={"detail": "bad key"})
                if request.headers.get("x-owner-key"):
                    f.bad.append("owner key sent")
                return fn(request, **kw)
            return inner

        m.get(f"{BRAND}/facts/v2").mock(side_effect=guard(
            lambda r: httpx.Response(503) if f.down else httpx.Response(200, json={"facts": list(f.facts.values())})))

        def detail(r, key):
            return httpx.Response(200, json={**f.facts[key], "versions": f.versions.get(key, [])})
        m.get(url__regex=rf"^{BRAND}/facts/v2/(?P<key>[^/]+)$").mock(side_effect=guard(detail))

        def post(r):
            body = json.loads(r.content)
            f.writes.append(("POST", "/facts/v2", body))
            if f.post_status >= 300:
                return httpx.Response(f.post_status, json={"detail": "no"})
            return httpx.Response(200, json={"key": body["key"], "version": 1, "status": "draft"})
        m.post(f"{BRAND}/facts/v2").mock(side_effect=guard(post))

        def put(r, key):
            body = json.loads(r.content)
            f.writes.append(("PUT", f"/facts/v2/{key}", body))
            return httpx.Response(200, json={"key": key, "version": 1, "latest_version": 2})
        m.put(url__regex=rf"^{BRAND}/facts/v2/(?P<key>[^/]+)$").mock(side_effect=guard(put))

        for name, route in {
            "import": m.post(f"{BRAND}/facts/v2/import"),
            "confirm": m.post(url__regex=rf"^{BRAND}/facts/v2/[^/]+/confirm$"),
            "retire": m.post(url__regex=rf"^{BRAND}/facts/v2/[^/]+/retire$"),
        }.items():
            route.mock(side_effect=lambda r, n=name: (f.bad.append(n), httpx.Response(599))[1])
        yield f


def make_cfg(**kw):
    return config.Config(**{"internal_api_key": KEY, "brand_url": BRAND, "mappings_file": EXAMPLE, **kw})


def make_env(odoo, **over):
    env = {"ODOO_URL": odoo.url, "ODOO_DB": ODOO_DB, "ODOO_LOGIN": ODOO_LOGIN, "ODOO_KEY": ODOO_KEY}
    env.update(over)
    return env


@pytest.fixture
def client(odoo, b05):
    from fastapi.testclient import TestClient
    app = create_app(make_cfg(), env=make_env(odoo))
    with TestClient(app) as c:
        c.headers["X-API-Key"] = KEY
        yield c
