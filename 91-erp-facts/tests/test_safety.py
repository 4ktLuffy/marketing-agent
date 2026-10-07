import logging

import pytest

from app import config, mapping
from app import odoo as odoo_mod
from app.main import create_app
from .conftest import ODOO_DB, ODOO_KEY, ODOO_LOGIN, make_cfg, make_env

SENTINELS = (ODOO_KEY, ODOO_LOGIN, ODOO_DB)


def _client(odoo_fake):
    return odoo_mod.OdooClient(odoo_fake.url, ODOO_DB, ODOO_LOGIN, ODOO_KEY)


@pytest.mark.parametrize("method", ["write", "create", "unlink", "call_kw", "execute", "execute_kw", "copy",
                                    "action_confirm", "name_create", "load", "import_data", ""])
def test_allowlist_blocks_every_other_method_before_any_network(odoo, method):
    c = _client(odoo)
    with pytest.raises(odoo_mod.OdooBlocked):
        c.call(method, [1], model="product.template")
    assert odoo.calls == []  # nothing reached the server, not even authenticate


def test_allowlist_is_exactly_the_seven_read_methods():
    assert odoo_mod.ALLOWED_METHODS == {"authenticate", "version", "search_read", "read", "search", "search_count",
                                    "fields_get"}


def test_client_has_no_write_helpers():
    for name in ("write", "create", "unlink", "call_kw", "execute", "execute_kw"):
        assert not hasattr(odoo_mod.OdooClient, name)


def test_allowed_methods_work_against_the_fake(odoo):
    c = _client(odoo)
    assert c.version()["server_version"] == "17.0"
    assert c.authenticate() == 7
    assert c.search_read("product.template", [["company_id", "=", 3]], ["name"])
    c.read("product.template", [20], ["name"])
    c.search("product.template", [])
    c.search_count("product.template", [])
    c.fields_get("product.template")
    assert {x["method"] for x in odoo.calls} <= odoo_mod.ALLOWED_METHODS | set()


@pytest.mark.parametrize("model", ["res.partner", "res.users", "hr.employee", "hr.contract", "mail.message",
                                   "account.move", "account.move.line", "sale.order"])
def test_denied_models_refused_by_client_without_network(odoo, model):
    c = _client(odoo)
    with pytest.raises(odoo_mod.OdooBlocked):
        c.search_read(model, [], ["name"])
    assert odoo.calls == []


def test_denied_models_refused_in_mapping_unless_allowed():
    def cfg(model, allow=None):
        d = {"mappings": [{"name": "x", "model": model, "domain": [], "fields": ["name", "list_price"],
                           "subject": {"kind": "product", "ref_template": "{name}"}, "key_template": "p-{name|slug}",
                           "value_field": "list_price", "text_template": "{name}"}]}
        if allow:
            d["allow_models"] = allow
        return d
    with pytest.raises(mapping.MappingError, match="personal data"):
        mapping.parse(cfg("res.partner"))
    with pytest.raises(mapping.MappingError):
        mapping.parse(cfg("sale.order"))
    assert mapping.parse(cfg("product.template"))
    assert mapping.parse(cfg("sale.order", ["sale.order"]))  # explicit allow


def test_personal_looking_fields_refused():
    d = {"mappings": [{"name": "x", "model": "product.template", "domain": [["partner_id", "=", 1]],
                       "fields": ["name", "list_price", "partner_id", "email"],
                       "subject": {"kind": "product"}, "key_template": "p-{name|slug}", "value_field": "list_price",
                       "text_template": "{name}"}]}
    with pytest.raises(mapping.MappingError) as e:
        mapping.parse(d)
    assert "partner_id" in str(e.value) and "email" in str(e.value)


def test_only_listed_fields_are_read(client, odoo):
    client.post("/sync?mapping=service-plans&dry_run=true")
    reads = [c for c in odoo.calls if c["method"] == "search_read"]
    allowed = {"name", "default_code", "list_price", "standard_price", "currency_id", "company_id", "write_date"}
    assert reads and all(set(c["fields"]) == allowed for c in reads)
    assert "partner_id" not in allowed


def test_no_credentials_in_responses_logs_or_repr(client, odoo, b05, caplog):
    caplog.set_level(logging.DEBUG)
    texts = []
    for url in ("/sync?dry_run=false", "/drift", "/health", "/sync?mapping=nope"):
        r = client.post(url) if url.startswith("/sync") else client.get(url)
        texts.append(r.text)
    c = odoo_client(odoo)
    texts += [repr(c), str(c), f"{c!r}"]
    blob = " ".join(texts) + caplog.text
    for s in SENTINELS:
        assert s not in blob
    assert odoo.url not in blob


def odoo_client(o):
    return _client(o)


def test_wrong_credentials_error_does_not_leak(odoo, b05):
    from fastapi.testclient import TestClient
    app = create_app(make_cfg(), env=make_env(odoo, ODOO_KEY="SENTINEL-WRONG-KEY"))
    with TestClient(app) as c:
        r = c.post("/sync?dry_run=true", headers={"X-API-Key": "internal-api-key-1"})
    assert r.status_code == 200 and r.json()["errors"]
    assert "SENTINEL-WRONG-KEY" not in r.text and ODOO_KEY not in r.text


def test_credentials_from_env_file(tmp_path, odoo, b05):
    f = tmp_path / "odoo.env"
    f.write_text(f"# c\nODOO_URL={odoo.url}\nODOO_DB={ODOO_DB}\nexport ODOO_LOGIN='{ODOO_LOGIN}'\nODOO_KEY={ODOO_KEY}\n")
    creds = odoo_creds({"ODOO_ENV_FILE": str(f)})
    assert creds["ODOO_KEY"] == ODOO_KEY
    assert odoo_creds({}) is None


def odoo_creds(env):
    return odoo_mod.load_credentials(env)


def test_plain_http_non_local_url_refused():
    with pytest.raises(odoo_mod.OdooError):
        odoo_mod.OdooClient("http://erp.example.com", "d", "l", "k")
    odoo_mod.OdooClient("https://erp.example.com", "d", "l", "k")


@pytest.mark.parametrize("name", ["FACT_OWNER_KEY", "APPROVER_KEY", "MY_OWNER_KEY"])
def test_refuses_to_start_with_owner_key(name):
    with pytest.raises(config.ConfigError, match=name):
        config.load({"INTERNAL_API_KEY": "k", name: "secret-value"})
    assert "secret-value" not in str(pytest.raises(config.ConfigError, config.load,
                                                   {"INTERNAL_API_KEY": "k", name: "secret-value"}).value)


def test_needs_internal_api_key():
    with pytest.raises(config.ConfigError):
        config.load({})
