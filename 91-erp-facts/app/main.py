"""erp-facts: read an ERP read-only, propose DRAFT facts to 05, report drift."""
import asyncio
import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from . import config as cfgmod
from . import mapping as mapmod
from . import odoo
from .sync import Syncer, UpstreamError

log = logging.getLogger("erp-facts")


def create_app(cfg: cfgmod.Config | None = None, mappings: mapmod.MappingSet | None = None,
               erp_factory=None, env=None) -> FastAPI:
    cfg = cfg or cfgmod.load(env)
    mappings = mappings or mapmod.load(cfg.mappings_file)
    environ = os.environ if env is None else env
    lock = threading.Lock()

    def default_erp():
        creds = odoo.load_credentials(environ)
        if not creds:
            raise UpstreamError("ERP credentials are not configured (ODOO_URL, ODOO_DB, ODOO_LOGIN, ODOO_KEY "
                                "or ODOO_ENV_FILE)", 503)
        try:
            return odoo.OdooClient(creds["ODOO_URL"], creds["ODOO_DB"], creds["ODOO_LOGIN"], creds["ODOO_KEY"],
                                   timeout=cfg.upstream_timeout, allow_models=mappings.allow_models)
        except odoo.OdooError as e:
            raise UpstreamError(str(e), 503) from None

    syncer = Syncer(cfg, mappings, erp_factory or default_erp)

    def run(name: str | None, dry_run: bool) -> dict:
        if name is not None and mappings.get(name) is None:
            raise HTTPException(404, f"no mapping named '{name}'")
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "a sync is already running")
        try:
            return syncer.run([name] if name else None, dry_run)
        except UpstreamError as e:
            raise HTTPException(e.status, str(e)) from None
        except (odoo.OdooError, odoo.OdooBlocked) as e:
            raise HTTPException(502, str(e)) from None
        finally:
            lock.release()

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if cfg.sync_every_min > 0:
            async def loop():
                while True:
                    await asyncio.sleep(cfg.sync_every_min * 60)
                    try:
                        # the loop only ever compares and writes drafts, like POST /sync
                        await asyncio.to_thread(run, None, False)
                    except Exception as e:  # never die; the next tick retries
                        log.warning("scheduled sync failed: %s", getattr(e, "detail", type(e).__name__))
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="erp-facts", lifespan=lifespan)

    def require_key(x_api_key: str | None = Header(default=None)):
        if not (x_api_key and hmac.compare_digest(x_api_key, cfg.internal_api_key)):
            raise HTTPException(401, "missing or wrong X-API-Key")

    @app.get("/health")
    def health():
        return {"ok": True, "mappings": sorted(mappings.mappings),
                "erp_configured": odoo.load_credentials(environ) is not None,
                "sync_every_min": cfg.sync_every_min}

    @app.post("/sync", dependencies=[Depends(require_key)])
    def sync(mapping: str | None = None, dry_run: bool = Query(True)):
        return run(mapping, dry_run)

    @app.get("/drift", dependencies=[Depends(require_key)])
    def drift(mapping: str | None = None):
        return run(mapping, True)

    return app
