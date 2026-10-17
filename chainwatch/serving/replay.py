"""Replay controller and live injection (spec 13.2).

Labels are split off at load time: the engine receives plain ``Event`` objects; the controller keeps the
truth per event id for the UI overlay and the comparison panel. No seeking backwards.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import random
import threading
import time
from collections import deque

from chainwatch.config import Config, resolve_path
from chainwatch.data.io import iter_events, read_accounts
from chainwatch.serving.engine import LateEvent, ScoringEngine
from chainwatch.serving.inject import TYPOLOGIES, make_campaign

log = logging.getLogger(__name__)
TIER = {"allow": 0, "verify": 1, "hold": 2}


class ReplayStateError(RuntimeError):
    pass


class InjectLimit(RuntimeError):
    pass


class Comparison:
    """Per ground-truth ring: when B0 (one payment at a time) and G2+R first had 2 ring accounts at
    >= verify."""

    def __init__(self, min_accounts: int = 2, min_tier: str = "verify"):
        self.min_accounts = min_accounts
        self.min_tier = TIER[min_tier]
        self.rings: dict[str, dict] = {}
        self.member_of: dict[str, set] = {}

    def add_ring(
        self, ring_id: str, typology: str, first_ts: float, members: list[str], injected: bool
    ) -> None:
        self.rings[ring_id] = {
            "ring_id": ring_id,
            "typology": typology,
            "first_ts": first_ts,
            "exit_ts": None,
            "b0": set(),
            "g2r": set(),
            "b0_ts": None,
            "g2r_ts": None,
            "injected": injected,
            "seen": False,
        }
        for a in members:
            self.member_of.setdefault(a, set()).add(ring_id)

    def observe(self, ev, decision: dict, truth: dict | None) -> bool:
        changed = False
        if truth and truth.get("is_fraud") and truth.get("ring_id") in self.rings:
            r = self.rings[truth["ring_id"]]
            if not r["seen"]:
                r["seen"] = True
                changed = True
            if truth.get("dst_role") == "sink" and r["exit_ts"] is None:
                r["exit_ts"] = ev.ts
                changed = True
        g2_hit = TIER[decision["decision"]] >= self.min_tier
        sh = decision.get("shadow")
        b0_hit = sh is not None and TIER[sh["B0"]["decision"]] >= self.min_tier
        if not (g2_hit or b0_hit):
            return changed
        for acct in (ev.src, ev.dst):
            for rid in self.member_of.get(acct, ()):
                r = self.rings[rid]
                for key, hit in (("g2r", g2_hit), ("b0", b0_hit)):
                    if hit and acct not in r[key]:
                        r[key].add(acct)
                        if len(r[key]) >= self.min_accounts and r[key + "_ts"] is None:
                            r[key + "_ts"] = ev.ts
                        changed = True
        return changed

    def table(self) -> list[dict]:
        out = []
        for r in sorted(self.rings.values(), key=lambda r: r["first_ts"]):
            if not r["seen"]:
                continue
            out.append(
                {
                    k: r[k]
                    for k in ("ring_id", "typology", "first_ts", "exit_ts", "b0_ts", "g2r_ts", "injected")
                }
            )
        return out


class ReplayController:
    def __init__(self, cfg: Config, engine: ScoringEngine, hub=None):
        self.cfg = cfg
        self.engine = engine
        self.hub = hub
        rc = cfg.replay
        self.warmup_until = float(rc.warmup_until_ts)
        self.default_speed = float(rc.default_speed)
        self.speed = self.default_speed
        self.max_batch = int(cfg.serving.max_batch)
        self.inject_max = int(rc.inject_max_per_minute)
        self.inject_scale = float(rc.inject_time_scale)
        t0 = time.time()
        self.events = []
        self.truth: dict[int, dict] = {}
        for le in iter_events(resolve_path(rc.dataset)):
            self.events.append(le.event)
            self.truth[le.event.event_id] = le.truth()
        self.accounts = read_accounts(resolve_path(rc.accounts))
        with open(resolve_path(rc.rings), encoding="utf-8") as fh:
            self.truth_rings = json.load(fh)["rings"]
        log.info(
            "replay data: %d events, %d accounts, %d rings (%.1fs)",
            len(self.events),
            len(self.accounts),
            len(self.truth_rings),
            time.time() - t0,
        )
        used = set()
        for r in self.truth_rings:
            for role in r["accounts"].values():
                used.update(role)
        self.inject_victims = sorted(
            a["account_id"]
            for a in self.accounts
            if a["archetype"] == "customer" and not a["is_mule"] and a["account_id"] not in used
        )
        self.inject_sinks = sorted(a["account_id"] for a in self.accounts if a["archetype"] == "agent")
        self._lock = threading.RLock()
        self._inject_times: deque = deque()
        self._inject_n = 0
        self._mule_n = 0
        self._next_injected_id = 10_000_000
        self._task: asyncio.Task | None = None
        self._init_state()

    def _init_state(self) -> None:
        self.state = "idle"
        self.position = 0
        self.sim_ts = self.events[0].ts if self.events else 0.0
        self.lag_s = 0.0
        self.pending: list = []  # heap of (ts, event_id, Event) injected events
        self.warmed = False
        self.comparison = Comparison(
            int(self.cfg.eval.catch_min_accounts), str(self.cfg.eval.catch_min_action)
        )
        for r in self.truth_rings:
            members = r["accounts"].get("mules", []) + r["accounts"].get("collectors", [])
            self.comparison.add_ring(r["ring_id"], r["typology"], r["first_ts"], members, False)
        self.comparison_dirty = True

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "state": self.state,
            "sim_ts": round(self.sim_ts, 3),
            "speed": self.speed,
            "position": self.position,
            "total": len(self.events),
            "lag_s": round(self.lag_s, 1),
            "warmed": self.warmed,
            "pending_injected": len(self.pending),
        }

    # ------------------------------------------------------------------ processing helpers
    def _process(self, ev, stream: bool, items: list, ring_events: list, warmup: bool = False) -> None:
        try:
            decision, revs, _ = self.engine.process(ev, warmup=warmup)
        except LateEvent:
            return
        truth = self.truth.get(ev.event_id)
        if self.comparison.observe(ev, decision, truth):
            self.comparison_dirty = True
        if stream:
            items.append({"event": ev.to_dict(), "decision": decision, "truth": truth})
            for e in revs:
                ring = e.ring
                if ring is not None:
                    ring = dict(ring)
                    ring["truth"] = self._ring_truth(ring)
                ring_events.append({"kind": e.kind, "ts": e.ts, "ring": ring})

    def _ring_truth(self, ring: dict) -> dict | None:
        """Ground-truth overlay for the UI: the planted ring sharing most flagged edges, and the purity."""
        ids = [e["event_id"] for e in ring.get("edges", [])]
        if not ids:
            return None
        counts: dict[str, int] = {}
        fraud = 0
        for i in ids:
            t = self.truth.get(i)
            if t and t["is_fraud"]:
                fraud += 1
                counts[t["ring_id"]] = counts.get(t["ring_id"], 0) + 1
        best = max(counts, key=lambda k: (counts[k], k)) if counts else None
        typ = None
        if best is not None:
            for r in self.truth_rings:
                if r["ring_id"] == best:
                    typ = r["typology"]
            typ = typ or ("injected" if str(best).startswith("INJ") else None)
        return {"ring_id": best, "typology": typ, "purity": round(fraud / len(ids), 3)}

    def _next_ts(self) -> float | None:
        a = self.events[self.position].ts if self.position < len(self.events) else None
        b = self.pending[0][0] if self.pending else None
        if a is None:
            return b
        if b is None:
            return a
        return min(a, b)

    def _pop_next(self):
        a = self.events[self.position] if self.position < len(self.events) else None
        if self.pending and (a is None or (self.pending[0][0], self.pending[0][1]) < (a.ts, a.event_id)):
            return heapq.heappop(self.pending)[2]
        self.position += 1
        return a

    # ------------------------------------------------------------------ control
    def warmup(self) -> None:
        """Process events before ``warmup_until_ts`` silently (state built, not streamed)."""
        with self._lock:
            if self.warmed:
                return
            t0 = time.time()
            n = 0
            while self.position < len(self.events) and self.events[self.position].ts < self.warmup_until:
                self._process(self.events[self.position], False, [], [], warmup=True)
                self.position += 1
                n += 1
            self.sim_ts = max(self.sim_ts, self.warmup_until)
            self.warmed = True
            log.info("replay warm-up: %d events in %.1fs", n, time.time() - t0)

    async def start(self, speed: float | None = None) -> dict:
        if self.state != "idle":
            await self.reset()
        if speed is not None:
            self.set_speed(speed)
        await asyncio.to_thread(self.warmup)
        self.state = "running"
        self._last_wall = time.monotonic()
        return self.status()

    def pause(self) -> dict:
        if self.state != "running":
            raise ReplayStateError(f"cannot pause in state {self.state}")
        self.state = "paused"
        return self.status()

    def resume(self) -> dict:
        if self.state != "paused":
            raise ReplayStateError(f"cannot resume in state {self.state}")
        self.state = "running"
        self._last_wall = time.monotonic()
        return self.status()

    def set_speed(self, speed: float) -> dict:
        if not 1.0 <= float(speed) <= 5000.0:
            raise ValueError("speed must be between 1 and 5000")
        self.speed = float(speed)
        return self.status()

    async def reset(self) -> dict:
        def _do():
            with self._lock:
                self.engine.reload()
                self._init_state()

        await asyncio.to_thread(_do)
        return self.status()

    async def next_campaign(self) -> dict:
        if self.state not in ("running", "paused"):
            raise ReplayStateError(f"cannot fast-forward in state {self.state}")
        target = None
        for r in sorted(self.truth_rings, key=lambda r: r["first_ts"]):
            if r["first_ts"] - 600.0 > self.sim_ts:
                target = r["first_ts"] - 600.0
                break
        if target is None:
            raise ReplayStateError("no later campaign in the replay data")
        was = self.state
        self.state = "paused"

        def _ff() -> int:
            with self._lock:
                n = 0
                while True:
                    nt = self._next_ts()
                    if nt is None or nt > target:
                        break
                    ev = self._pop_next()
                    self._process(ev, False, [], [])
                    n += 1
                self.sim_ts = target
                return n

        skipped = await asyncio.to_thread(_ff)
        self.state = was
        self._last_wall = time.monotonic()
        if self.hub is not None:
            self.hub.send_now(
                {
                    "type": "fastforward",
                    "to_ts": target,
                    "skipped": skipped,
                    "rings": self.engine.grouper.rings(("open", "alert")),
                    "replay": self.status(),
                }
            )
        return {**self.status(), "skipped": skipped}

    def inject(self, typology: str, seed: int = 1) -> dict:
        if typology not in TYPOLOGIES:
            raise ValueError(f"unknown typology {typology!r}; use one of {', '.join(TYPOLOGIES)}")
        if self.state not in ("running", "paused"):
            raise ReplayStateError("injection needs the replay to be running or paused")
        now = time.monotonic()
        while self._inject_times and now - self._inject_times[0] > 60.0:
            self._inject_times.popleft()
        if len(self._inject_times) >= self.inject_max:
            raise InjectLimit(f"at most {self.inject_max} injected campaigns per minute")
        self._inject_times.append(now)
        rng = random.Random(int(seed) * 7919 + self._inject_n)
        self._inject_n += 1
        ring_id = f"INJ{self._inject_n}"
        nv = min(60, 3 + int(rng.expovariate(1.0 / 7.0)))
        if typology == "layered_chain":
            nv = min(nv, 15)
        victims = rng.sample(self.inject_victims, min(nv, len(self.inject_victims)))
        sink = rng.choice(self.inject_sinks)
        mules: list[str] = []

        def new_mule() -> str:
            self._mule_n += 1
            m = f"X{self._mule_n:04d}"
            mules.append(m)
            return m

        start = self.sim_ts + 5.0
        evs = make_campaign(
            typology,
            rng,
            start,
            victims,
            new_mule,
            sink,
            "CASH_OUT",
            self.inject_scale,
            ring_id,
            self._next_injected_id,
        )
        self._next_injected_id += len(evs) + 1
        with self._lock:
            for le in evs:
                heapq.heappush(self.pending, (le.event.ts, le.event.event_id, le.event))
                self.truth[le.event.event_id] = le.truth(injected=True)
            collectors = sorted({le.event.dst for le in evs if le.dst_role == "collector"})
            self.comparison.add_ring(ring_id, typology, start, mules, True)
            self.truth_rings.append(
                {
                    "ring_id": ring_id,
                    "typology": typology,
                    "first_ts": evs[0].event.ts,
                    "accounts": {
                        "victims": victims,
                        "mules": sorted(set(mules) - set(collectors)),
                        "collectors": collectors,
                        "sinks": [sink],
                    },
                    "injected": True,
                }
            )
        return {"ring_id": ring_id, "n_events": len(evs), "starts_at": evs[0].event.ts if evs else start}

    # ------------------------------------------------------------------ clock loop
    async def run(self) -> None:
        self._last_wall = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            if self.state != "running":
                self._last_wall = time.monotonic()
                continue
            now = time.monotonic()
            dt = now - self._last_wall
            self._last_wall = now
            items: list = []
            ring_events: list = []
            with self._lock:
                self.sim_ts += self.speed * dt
                k = 0
                while k < self.max_batch:
                    nt = self._next_ts()
                    if nt is None or nt > self.sim_ts:
                        break
                    self._process(self._pop_next(), True, items, ring_events)
                    k += 1
                nt = self._next_ts()
                if nt is not None and nt <= self.sim_ts:
                    self.lag_s = self.sim_ts - nt
                    self.sim_ts = min(self.sim_ts, nt + self.speed)  # cap the clock; never drop events
                else:
                    self.lag_s = 0.0
                if nt is None:
                    self.state = "finished"
            if self.hub is not None and (items or ring_events):
                self.hub.publish(items, ring_events)

    def start_loop(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run())

    async def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
