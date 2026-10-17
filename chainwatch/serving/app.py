"""FastAPI app factory (spec 13.6). Start-up never downloads anything; if loading fails the process
stays up and ``/healthz`` reports ``ready: false`` with a short detail.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from chainwatch import __version__
from chainwatch.config import Config, load
from chainwatch.serving.routes import ApiError, error_response, info_payload, router, v1
from chainwatch.serving.security import ApiKeyGuard, RateLimiter
from chainwatch.serving.ws import WsHub

log = logging.getLogger("chainwatch.serving")
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 1_048_576


class _State:
    def __init__(self, cfg: Config | None):
        self.t0 = time.time()
        self.cfg = cfg
        self.ready = False
        self.detail = "starting"
        self.engine = None
        self.replay = None
        self.hub = None
        self.static_dir = STATIC_DIR
        env = cfg.serving.api_key_env if cfg else "CHAINWATCH_API_KEY"
        self.guard = ApiKeyGuard(env)
        self.limiter = RateLimiter(float(cfg.serving.rate_limit_rps) if cfg else 0.0)
        self.max_batch = int(cfg.serving.max_batch) if cfg else 500


class _HeadersAndLimit:
    """Pure ASGI middleware: 1 MB request-body limit (by Content-Length) and ``Cache-Control: no-store``."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > MAX_BODY:
                        resp = error_response(400, "bad_request", "request body larger than 1 MB")
                        return await resp(scope, receive, send)
                except ValueError:
                    pass

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, send_wrapper)


def create_app(
    config_path: str | Path | None = None,
    model_dir: str | Path | None = None,
    replay: bool = True,
    compare: bool | None = None,
    cfg: Config | None = None,
) -> FastAPI:
    """Build the app. Models, thresholds, profile, reasons and (optionally) replay data load in the
    lifespan."""
    try:
        cfg = cfg or load(config_path)
        boot_error = None
    except Exception as e:  # config errors still give a live /healthz
        cfg, boot_error = None, f"config: {e}"
    state = _State(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from chainwatch.serving.engine import ScoringEngine
        from chainwatch.serving.replay import ReplayController

        t0 = time.time()
        warm_task = None
        try:
            if boot_error:
                raise RuntimeError(boot_error)
            state.engine = ScoringEngine(cfg, model_dir, compare)
            log.info("models loaded from %s (%.1fs)", state.engine.model_dir, time.time() - t0)

            def snapshot():
                out = {
                    "info": info_payload(state),
                    "rings": state.engine.rings(("open", "alert"), 50, True),
                    "metrics": state.engine.metrics_snapshot(),
                }
                if state.replay is not None:
                    out["replay"] = state.replay.status()
                    out["comparison"] = state.replay.comparison.table()
                return out

            def extras():
                out = {"metrics": state.engine.metrics_snapshot()}
                if state.replay is not None:
                    out["replay"] = state.replay.status()
                    if state.replay.comparison_dirty:
                        state.replay.comparison_dirty = False
                        out["comparison"] = state.replay.comparison.table()
                return out

            state.hub = WsHub(int(cfg.serving.ws_batch_ms), snapshot, extras)
            state.hub.start()
            if replay:
                state.replay = ReplayController(cfg, state.engine, state.hub)
                state.replay.start_loop()
                warm_task = asyncio.get_running_loop().run_in_executor(None, state.replay.warmup)
                log.info("replay controller ready (idle); warm-up running in the background")
            state.ready = True
            state.detail = None
            log.info("ready in %.1fs", time.time() - t0)
        except Exception as e:
            state.ready = False
            state.detail = f"{type(e).__name__}: {e}"[:500]
            log.exception("start-up failed; /healthz reports ready=false")
        yield
        if state.replay is not None:
            await state.replay.stop_loop()
        if state.hub is not None:
            await state.hub.stop()
        if warm_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(warm_task), timeout=0.1)
            except Exception:
                pass

    app = FastAPI(
        title="ChainWatch",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        description="Mule-ring detection on synthetic payment streams. Recommendations only.",
    )
    app.state.cw = state

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        parts = []
        for err in exc.errors()[:5]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg')}")
        return error_response(422, "bad_request", "; ".join(parts) or "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {401: "unauthorized", 404: "not_found", 429: "rate_limited", 503: "not_ready"}.get(
            exc.status_code, "bad_request"
        )
        return error_response(exc.status_code, code, str(exc.detail))

    app.include_router(router)
    app.include_router(v1)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if cfg is not None and cfg.serving.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.serving.cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(_HeadersAndLimit)
    return app
