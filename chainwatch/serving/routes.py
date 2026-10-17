"""REST and WebSocket endpoints (spec 13.1)."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from chainwatch import __version__
from chainwatch.schema import EventIn
from chainwatch.serving.engine import LateEvent
from chainwatch.serving.replay import InjectLimit, ReplayStateError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def error_response(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


class SpeedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: float


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: float | None = None


class InjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    typology: str
    seed: int = 1


class CompareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _st(request: Request):
    return request.app.state.cw


async def v1_guard(request: Request) -> None:
    st = _st(request)
    if not st.guard.check(request.headers.get("x-api-key")):
        raise ApiError(401, "unauthorized", "missing or wrong X-API-Key")
    client = request.client.host if request.client else "?"
    if not st.limiter.allow(client):
        raise ApiError(429, "rate_limited", "too many requests")
    if not st.ready:
        raise ApiError(503, "not_ready", st.detail or "service is starting")


def _replay(request: Request):
    ctl = _st(request).replay
    if ctl is None:
        raise ApiError(409, "replay_state", "replay is disabled (started with --no-replay)")
    return ctl


router = APIRouter()
v1 = APIRouter(prefix="/v1", dependencies=[Depends(v1_guard)])


@router.get("/healthz")
@router.get("/health")
@router.get("/ping")
@router.get("/ready")
@v1.get("/healthz")
@v1.get("/health")
@v1.get("/ping")
async def healthz(request: Request):
    st = _st(request)
    body = {
        "status": "ok",
        "ready": st.ready,
        "version": __version__,
        "uptime_s": round(time.time() - st.t0, 1),
    }
    if not st.ready and st.detail:
        body["detail"] = st.detail
    return body


@router.get("/")
async def index(request: Request):
    return FileResponse(_st(request).static_dir / "index.html", headers={"Cache-Control": "no-store"})


def info_payload(st) -> dict:
    eng = st.engine
    out = {
        "service": "chainwatch",
        "version": __version__,
        "mode": "replay" if st.replay else "api",
        "provenance": f"{eng.pkg.provenance} (synthetic)"
        if eng.pkg.provenance == "ChainWorld"
        else eng.pkg.provenance,
    }
    out.update(eng.info())
    out["auth_required"] = st.guard.required
    out["tz_offset_s"] = float(eng.cfg.project.tz_offset_s)
    return out


@v1.get("/info")
@router.get("/info", dependencies=[Depends(v1_guard)])
async def info(request: Request):
    return info_payload(_st(request))


@v1.post("/score")
@v1.post("/predict")
@router.post("/score", dependencies=[Depends(v1_guard)])
@router.post("/predict", dependencies=[Depends(v1_guard)])
async def score(body: EventIn, request: Request):
    st = _st(request)
    ev = body.to_event()
    try:
        decision, ring_events, replayed = st.engine.process(ev)
    except LateEvent as e:
        raise ApiError(409, "late_event", str(e)) from None
    if not replayed and st.hub is not None:
        st.hub.publish(
            [{"event": ev.to_dict(), "decision": decision, "truth": None}],
            [{"kind": e.kind, "ts": e.ts, "ring": e.ring} for e in ring_events],
        )
    headers = {"X-Idempotent-Replay": "true"} if replayed else None
    return JSONResponse(decision, headers=headers)


@v1.post("/score/batch")
@router.post("/score/batch", dependencies=[Depends(v1_guard)])
async def score_batch(request: Request):
    st = _st(request)
    try:
        payload = await request.json()
    except Exception:
        raise ApiError(400, "bad_request", "body must be JSON") from None
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list) or set(payload) - {"events"}:
        raise ApiError(400, "bad_request", 'body must be {"events": [...]}')
    if len(events) > st.max_batch:
        raise ApiError(400, "bad_request", f"at most {st.max_batch} events per batch")
    results = []
    items, revs = [], []
    for i, item in enumerate(events):
        if i and i % 50 == 0:
            await asyncio.sleep(0)
        try:
            ev = EventIn.model_validate(item).to_event()
        except ValidationError as e:
            results.append({"error": {"code": "bad_request", "message": _short_validation(e)}})
            continue
        try:
            decision, ring_events, replayed = st.engine.process(ev)
        except LateEvent as e:
            results.append({"error": {"code": "late_event", "message": str(e)}})
            continue
        results.append(decision)
        if not replayed:
            items.append({"event": ev.to_dict(), "decision": decision, "truth": None})
            revs.extend({"kind": e.kind, "ts": e.ts, "ring": e.ring} for e in ring_events)
    if st.hub is not None and (items or revs):
        st.hub.publish(items, revs)
    return {"results": results}


def _short_validation(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


@v1.get("/rings")
@router.get("/rings", dependencies=[Depends(v1_guard)])
async def rings(
    request: Request, status: str = "alert,open", limit: int = Query(50, ge=0, le=1000), detail: bool = False
):
    wanted = tuple(s.strip() for s in status.split(",") if s.strip())
    bad = set(wanted) - {"open", "alert", "closed"}
    if bad:
        raise ApiError(400, "bad_request", f"unknown status {sorted(bad)}")
    return {"rings": _st(request).engine.rings(wanted, limit, detail)}


@v1.get("/rings/{ring_id}")
@router.get("/rings/{ring_id}", dependencies=[Depends(v1_guard)])
async def ring(ring_id: str, request: Request):
    snap = _st(request).engine.ring(ring_id)
    if snap is None:
        raise ApiError(404, "not_found", f"unknown ring {ring_id}")
    return snap


@v1.get("/accounts/{account_id}")
async def account(account_id: str, request: Request):
    view = _st(request).engine.account(account_id)
    if view is None:
        raise ApiError(404, "not_found", f"account {account_id} has not been seen")
    return view


@v1.get("/metrics")
@router.get("/metrics", dependencies=[Depends(v1_guard)])
async def metrics(request: Request):
    return _st(request).engine.metrics_snapshot()


@v1.post("/compare")
async def compare(body: CompareIn, request: Request):
    st = _st(request)
    st.engine.compare = bool(body.enabled)
    return {"compare_mode": st.engine.compare}


@v1.get("/replay/status")
async def replay_status(request: Request):
    return _replay(request).status()


@v1.get("/replay/comparison")
async def replay_comparison(request: Request):
    ctl = _replay(request)
    return {"compare_mode": _st(request).engine.compare, "rings": ctl.comparison.table()}


@v1.post("/replay/start")
async def replay_start(request: Request):
    ctl = _replay(request)
    speed = None
    raw = await request.body()
    if raw.strip():
        try:
            speed = StartIn.model_validate_json(raw).speed
        except ValidationError as e:
            raise ApiError(422, "bad_request", _short_validation(e)) from None
    try:
        return await ctl.start(speed)
    except ValueError as e:
        raise ApiError(400, "bad_request", str(e)) from None


@v1.post("/replay/pause")
async def replay_pause(request: Request):
    try:
        return _replay(request).pause()
    except ReplayStateError as e:
        raise ApiError(409, "replay_state", str(e)) from None


@v1.post("/replay/resume")
async def replay_resume(request: Request):
    try:
        return _replay(request).resume()
    except ReplayStateError as e:
        raise ApiError(409, "replay_state", str(e)) from None


@v1.post("/replay/speed")
async def replay_speed(body: SpeedIn, request: Request):
    try:
        return _replay(request).set_speed(body.speed)
    except ValueError as e:
        raise ApiError(400, "bad_request", str(e)) from None


@v1.post("/replay/next_campaign")
async def replay_next(request: Request):
    try:
        return await _replay(request).next_campaign()
    except ReplayStateError as e:
        raise ApiError(409, "replay_state", str(e)) from None


@v1.post("/replay/reset")
async def replay_reset(request: Request):
    ctl = _replay(request)
    out = await ctl.reset()
    asyncio.get_running_loop().run_in_executor(None, ctl.warmup)
    return out


@v1.post("/inject/scenario")
async def inject(body: InjectIn, request: Request):
    try:
        return _replay(request).inject(body.typology, body.seed)
    except InjectLimit as e:
        raise ApiError(429, "rate_limited", str(e)) from None
    except ReplayStateError as e:
        raise ApiError(409, "replay_state", str(e)) from None
    except ValueError as e:
        raise ApiError(400, "bad_request", str(e)) from None


@router.websocket("/v1/ws")
async def websocket(ws: WebSocket):
    st = ws.app.state.cw
    if not st.guard.check(ws.query_params.get("key") or ws.headers.get("x-api-key")):
        await ws.close(code=4401)
        return
    if not st.ready or st.hub is None:
        await ws.close(code=4503)
        return
    await ws.accept()
    await st.hub.serve(ws)
