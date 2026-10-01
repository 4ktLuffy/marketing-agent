"""Read-only Odoo XML-RPC client. The ONLY place that talks to the ERP.

Safety, enforced here and nowhere else:

- `ALLOWED_METHODS` is the complete list of Odoo methods this service can call. Anything else
  raises `OdooBlocked` BEFORE any network call: no write, create, unlink, call_kw, or execute
  of a method outside the list.
- `check_model` refuses models that hold personal data unless a config explicitly allows them.
- Credentials live only in this object. repr/str mask them; errors never include them.
"""
from __future__ import annotations

import os
import re
import xmlrpc.client
from urllib.parse import urlparse

ALLOWED_METHODS = frozenset({"authenticate", "version", "search_read", "read", "search", "search_count",
                             "fields_get"})
# Methods that go to /xmlrpc/2/common; the rest go to /xmlrpc/2/object (execute_kw).
COMMON_METHODS = frozenset({"authenticate", "version"})

# Models refused unless listed in `allow_models` of the mappings file.
DENY_MODELS = ("res.partner", "res.users", "sale.order", "hr.*", "mail.*", "account.move*")
# Field names that suggest personal data; a mapping may not read them.
DENY_FIELD_RE = re.compile(r"(partner|customer|employee|user|email|phone|mobile|vat|street|city|birth|salary|"
                           r"password|token|passport|iban|bank)", re.I)

CRED_NAMES = ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_KEY")


class OdooBlocked(Exception):
    """A call the allowlist or the model deny list refuses. Raised before any network call."""


class OdooError(Exception):
    """The ERP failed or answered badly. The message never contains a credential."""


def check_method(method: str) -> None:
    if method not in ALLOWED_METHODS:
        raise OdooBlocked(f"Odoo method '{method}' is not allowed (read-only: {', '.join(sorted(ALLOWED_METHODS))})")


def model_denied(model: str, allow: tuple[str, ...] | list[str] = ()) -> bool:
    if model in allow:
        return False
    for pat in DENY_MODELS:
        if pat.endswith("*"):
            if model.startswith(pat[:-1]):
                return True
        elif model == pat:
            return True
    return False


def check_model(model: str, allow=()) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)*", model or ""):
        raise OdooBlocked(f"'{model}' is not a model name")
    if model_denied(model, allow):
        raise OdooBlocked(f"model '{model}' may hold personal data and is refused "
                          f"(list it under allow_models to permit it)")


def check_field(field: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", field or ""):
        raise OdooBlocked(f"'{field}' is not a plain field name")
    if DENY_FIELD_RE.search(field):
        raise OdooBlocked(f"field '{field}' looks like personal data and is refused")


def load_credentials(env: dict | None = None) -> dict[str, str] | None:
    """ODOO_URL/DB/LOGIN/KEY from env, filling gaps from the file named by ODOO_ENV_FILE.
    Returns None when any is missing. Never reports values."""
    env = os.environ if env is None else env
    vals = {n: (env.get(n) or "").strip() for n in CRED_NAMES}
    path = (env.get("ODOO_ENV_FILE") or "").strip()
    if path and not all(vals.values()):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip().removeprefix("export ").strip()
                    if k in vals and not vals[k]:
                        vals[k] = v.strip().strip("'\"")
        except OSError:
            raise OdooError("ODOO_ENV_FILE cannot be read") from None
    return vals if all(vals.values()) else None


def _https_or_loopback(url: str) -> str:
    u = urlparse(url)
    if u.scheme == "https" and u.hostname:
        return url.rstrip("/")
    if u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1"):
        return url.rstrip("/")
    raise OdooError("ODOO_URL must be https:// (plain http only for localhost)")


class OdooClient:
    def __init__(self, url: str, db: str, login: str, key: str, *, proxy_factory=None, timeout: float = 30.0,
                 allow_models=()):
        self._url = _https_or_loopback(url)
        self._db, self._login, self._key = db, login, key
        self._allow = tuple(allow_models)
        self._uid: int | None = None
        self._factory = proxy_factory or self._default_factory
        self._timeout = timeout

    # never show credentials
    def __repr__(self) -> str:
        return "OdooClient(url=***, db=***, login=***, key=***)"

    __str__ = __repr__

    def _default_factory(self, path: str):
        return xmlrpc.client.ServerProxy(self._url + path, allow_none=True,
                                         transport=_timeout_transport(self._url, self._timeout))

    def _clean(self, text: str) -> str:
        for secret in (self._key, self._login, self._db):
            if secret:
                text = text.replace(secret, "***")
        return text[:200]

    def _send(self, path: str, method: str, params: tuple):
        try:
            proxy = self._factory(path)
            return getattr(proxy, method)(*params)
        except xmlrpc.client.Fault as e:
            raise OdooError(f"Odoo refused {method}: {self._clean(str(e.faultString))}") from None
        except xmlrpc.client.ProtocolError as e:
            raise OdooError(f"Odoo answered {method} with HTTP {e.errcode}") from None
        except (OSError, xmlrpc.client.Error, ValueError) as e:
            raise OdooError(f"Odoo call {method} failed ({type(e).__name__})") from None

    def call(self, method: str, *args, model: str | None = None, **kwargs):
        """Every Odoo call goes through here. The allowlist check runs first."""
        check_method(method)
        if method == "version":
            return self._send("/xmlrpc/2/common", "version", ())
        if method == "authenticate":
            uid = self._send("/xmlrpc/2/common", "authenticate", (self._db, self._login, self._key, {}))
            if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
                raise OdooError("Odoo login failed (check ODOO_DB, ODOO_LOGIN, ODOO_KEY)")
            self._uid = uid
            return uid
        check_model(model or "", self._allow)
        if self._uid is None:
            self.call("authenticate")
        return self._send("/xmlrpc/2/object", "execute_kw",
                          (self._db, self._uid, self._key, model, method, list(args), kwargs))

    def authenticate(self) -> int:
        return self.call("authenticate")

    def version(self) -> dict:
        return self.call("version")

    def search_read(self, model: str, domain: list, fields: list[str], limit: int = 0) -> list[dict]:
        for f in fields:
            check_field(f)
        kw = {"fields": list(fields)}
        if limit:
            kw["limit"] = limit
        return self.call("search_read", domain, model=model, **kw)

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict]:
        for f in fields:
            check_field(f)
        return self.call("read", ids, model=model, fields=list(fields))

    def search(self, model: str, domain: list, limit: int = 0) -> list[int]:
        return self.call("search", domain, model=model, **({"limit": limit} if limit else {}))

    def search_count(self, model: str, domain: list) -> int:
        return self.call("search_count", domain, model=model)

    def fields_get(self, model: str, attributes: list[str] | None = None) -> dict:
        return self.call("fields_get", model=model, attributes=attributes or ["string", "type"])


def _timeout_transport(url: str, timeout: float):
    cls = xmlrpc.client.SafeTransport if url.startswith("https") else xmlrpc.client.Transport

    class T(cls):  # type: ignore[misc, valid-type]
        def make_connection(self, host):
            conn = super().make_connection(host)
            conn.timeout = timeout
            return conn
    return T()
