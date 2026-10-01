"""Read the ERP, build draft facts, compare with 05, optionally write DRAFTS to 05.

What this module never does: confirm, retire or import (import needs the owner key), send an
X-Owner-Key, or call the ERP with anything but the read-only client.
"""
from __future__ import annotations

import logging
import math

import httpx

from . import build as B
from .config import Config
from .mapping import MappingSet

log = logging.getLogger("erp-facts")


class UpstreamError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    return a == b


class Syncer:
    def __init__(self, cfg: Config, mappings: MappingSet, erp_factory):
        self.cfg, self.mappings, self.erp_factory = cfg, mappings, erp_factory

    # ---- 05 (only X-API-Key is ever sent) ----
    def _brand(self) -> httpx.Client:
        return httpx.Client(base_url=self.cfg.brand_url, timeout=self.cfg.upstream_timeout,
                            headers={"X-API-Key": self.cfg.internal_api_key})

    def _served(self, c: httpx.Client) -> dict[str, dict]:
        try:
            r = c.get("/facts/v2", params={"include_derived": "false"})
        except httpx.HTTPError:
            raise UpstreamError("the brand service (05) is not reachable") from None
        if r.status_code != 200:
            raise UpstreamError(f"the brand service (05) answered {r.status_code} to GET /facts/v2")
        return {f["key"]: f for f in r.json().get("facts", [])}

    def _latest_draft_value(self, c: httpx.Client, key: str, fact: dict):
        """(value, currency) of a newer unconfirmed version than the served one, if any."""
        if (fact.get("latest_version") or fact.get("version")) == fact.get("version"):
            return None
        try:
            r = c.get(f"/facts/v2/{key}")
            if r.status_code != 200:
                return None
            vs = r.json().get("versions", [])
            top = max(vs, key=lambda v: v["version"])
            return top["data"].get("value"), top["data"].get("currency")
        except (httpx.HTTPError, KeyError, ValueError):
            return None

    # ---- ERP ----
    def _read(self, erp, m) -> list[dict]:
        fields = list(dict.fromkeys([*m.fields, "write_date"]))
        return erp.search_read(m.model, m.domain, fields, limit=self.cfg.max_records)

    def run(self, names: list[str] | None, dry_run: bool) -> dict:
        chosen = [self.mappings.mappings[n] for n in (names or list(self.mappings.mappings))]
        out = {"dry_run": dry_run, "mappings": [m.name for m in chosen], "new": [], "changed": [],
               "unchanged": 0, "pending": [], "skipped": [], "errors": [],
               "written": {"created": 0, "updated": 0}}
        with self._brand() as c:
            served = self._served(c)
            erp = self.erp_factory()
            built: dict[str, B.Built] = {}
            for m in chosen:
                try:
                    records = self._read(erp, m)
                except Exception as e:  # OdooError / OdooBlocked: messages carry no secrets
                    out["errors"].append({"mapping": m.name, "error": str(e)})
                    continue
                for rec in records:
                    for spec in m.specs:
                        r = B.build(m, spec, rec)
                        if isinstance(r, B.Skip):
                            out["skipped"].append({"record": r.record, "reason": r.reason})
                        elif r.key in built:
                            out["skipped"].append({"record": r.record, "reason": f"duplicate key {r.key}"})
                        else:
                            built[r.key] = r
            for key, b in sorted(built.items()):
                cur = served.get(key)
                if cur is None:
                    out["new"].append({"key": key, "erp_value": b.value, "currency": b.currency})
                    self._write(c, out, "POST", b, dry_run)
                    continue
                if cur.get("status") in ("retired", "superseded"):
                    out["skipped"].append({"record": b.record, "reason": f"fact {key} is {cur['status']} in 05"})
                    continue
                if _same(cur.get("value"), b.value) and _same(cur.get("currency"), b.currency):
                    out["unchanged"] += 1
                    continue
                pend = self._latest_draft_value(c, key, cur)
                if pend and _same(pend[0], b.value) and _same(pend[1], b.currency):
                    out["pending"].append(key)  # the owner has not confirmed the draft yet
                    continue
                out["changed"].append({"key": key, "served_value": cur.get("value"), "erp_value": b.value,
                                       "erp_write_date": b.write_date})
                self._write(c, out, "PUT", b, dry_run)
        log.info("sync dry_run=%s new=%d changed=%d unchanged=%d skipped=%d errors=%d", dry_run,
                 len(out["new"]), len(out["changed"]), out["unchanged"], len(out["skipped"]), len(out["errors"]))
        return out

    def _write(self, c: httpx.Client, out: dict, method: str, b: B.Built, dry_run: bool) -> None:
        if dry_run:
            return
        try:
            if method == "POST":  # new key: a draft version 1
                r = c.post("/facts/v2", json=b.body)
            else:                 # existing key: a new draft version; the confirmed one stays served
                r = c.put(f"/facts/v2/{b.key}", json=b.body)
        except httpx.HTTPError:
            out["errors"].append({"key": b.key, "error": "the brand service (05) is not reachable"})
            return
        if r.status_code >= 300:
            out["errors"].append({"key": b.key, "error": f"05 answered {r.status_code}"})
        else:
            out["written"]["created" if method == "POST" else "updated"] += 1
