"""ScoringEngine: the one place that scores (spec 4). Used by REST, WebSocket replay and benchmarks.

Per event: idempotency, lateness, features, model, ring escalation from prior ring state, tier, reasons,
then the edge (base score) goes to the ring grouper. Single-threaded and stateful; ``process`` is guarded
by a lock (E8).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

from chainwatch.config import Config, resolve_path
from chainwatch.decision.reasons import ReasonEngine
from chainwatch.decision.tiers import RECOMMENDED_ACTION, Thresholds, decide, tier_from_score
from chainwatch.features.engine import FeatureEngine
from chainwatch.features.registry import registry_hash_for_cfg
from chainwatch.model.portable_gbdt import PortableGBDT
from chainwatch.model.profile import BenignProfile
from chainwatch.rings.grouping import RingGrouper
from chainwatch.schema import Event, LabeledEvent
from chainwatch.serving.metrics import Metrics

DETECTORS = ("B0", "B1", "G2")
PRODUCTION = "G2+R"


class LateEvent(Exception):
    pass


class RegistryMismatch(RuntimeError):
    pass


def model_dir_from_env(default: str = "models/default") -> Path:
    return resolve_path(os.environ.get("CHAINWATCH_MODEL_DIR", default))


class ModelPackage:
    """Portable models, thresholds, benign profile and manifest of one ``models/<name>/`` directory."""

    def __init__(self, model_dir: str | Path):
        self.dir = Path(model_dir)
        man_path = self.dir / "manifest.json"
        self.manifest = json.loads(man_path.read_text(encoding="utf-8"))
        self.model_version = hashlib.sha256(man_path.read_bytes()).hexdigest()[:8]
        self.models = {d: PortableGBDT.load(self.dir / f"{d}.json") for d in DETECTORS}
        self.thresholds = json.loads((self.dir / "thresholds.json").read_text(encoding="utf-8"))
        self.profile = BenignProfile.load(self.dir / "benign_profile.json")
        self.trained_on = self.manifest.get("trained_on")
        self.provenance = self.manifest.get("provenance", "ChainWorld")


class ScoringEngine:
    def __init__(self, cfg: Config, model_dir: str | Path | None = None, compare: bool | None = None):
        self.cfg = cfg
        self.model_dir = Path(model_dir) if model_dir else model_dir_from_env()
        self.compare = bool(cfg.serving.compare_mode if compare is None else compare)
        self.lru_cap = int(cfg.serving.request_id_lru)
        self.max_lateness = float(cfg.features.max_lateness_s)
        self.lock = threading.Lock()
        self.started = time.time()
        self._load()
        self.reset()

    def _load(self) -> None:
        cfg = self.cfg
        self.pkg = ModelPackage(self.model_dir)
        for d in DETECTORS:
            expected = registry_hash_for_cfg(cfg, d)
            got = self.pkg.models[d].registry_hash
            if got != expected:
                raise RegistryMismatch(
                    f"model {d} in {self.model_dir} was built with feature registry {got[:19]}..., "
                    f"but the current config gives {expected[:19]}...; retrain the model package "
                    f"with this config "
                    f"(make models) or start the server with the config the models were trained with"
                )
        self.names = {d: self.pkg.models[d].feature_names for d in DETECTORS}
        var = self.pkg.thresholds["variants"]
        self.thr = Thresholds.from_dict(var[PRODUCTION])
        self.shadow_thr = {d: Thresholds.from_dict(var[d]) for d in ("B0", "B1")}
        self.reasons = ReasonEngine(near_cap=float(cfg.features.near_cap_inr), profile=self.pkg.profile)

    def reload(self) -> None:
        """Reload the model package from disk and start from fresh state."""
        with self.lock:
            self._load()
            self.reset()

    def reset(self) -> None:
        """Fresh feature, ring and metric state (models stay loaded)."""
        self.features = FeatureEngine(self.cfg, DETECTORS)
        self.grouper = RingGrouper(self.cfg.rings, self.thr.edge, self.thr.ring_alert, snapshots=True)
        self.lru: OrderedDict[int, dict] = OrderedDict()
        self.metrics = Metrics(float(self.cfg.serving.latency_hist_max_ms))
        self.acct_recent: OrderedDict[str, deque] = OrderedDict()

    # ------------------------------------------------------------------ scoring
    def process(self, ev: Event, warmup: bool = False) -> tuple[dict, list, bool]:
        """Score one event. Returns ``(decision, ring_events, idempotent_replay)``."""
        if not isinstance(ev, Event) or isinstance(ev, LabeledEvent):
            raise TypeError("ScoringEngine.process expects an Event without labels")
        with self.lock:
            cached = self.lru.get(ev.event_id)
            if cached is not None:
                return cached, [], True
            if ev.ts < self.features.clock - self.max_lateness:
                raise LateEvent(
                    f"event {ev.event_id} at ts={ev.ts} is older than the engine clock "
                    f"{self.features.clock} minus max_lateness_s={self.max_lateness}"
                )
            t0 = time.perf_counter_ns()
            f = self.features.step(ev)
            t1 = time.perf_counter_ns()
            base = self.pkg.models["G2"].predict([f[n] for n in self.names["G2"]])
            shadow = None
            if self.compare:
                shadow = {}
                for d in ("B0", "B1"):
                    s = self.pkg.models[d].predict([f[n] for n in self.names[d]])
                    shadow[d] = {"score": s, "decision": tier_from_score(s, self.shadow_thr[d])}
            t2 = time.perf_counter_ns()
            ts_eff = self.features.clock
            g = self.grouper
            ring_events = g.tick(ts_eff)
            floor, esc_ring, side = g.floor_info(ev.src, ev.dst)
            tier, eff = decide(base, self.thr, floor)
            if base >= self.thr.edge:
                e2 = ev if ev.ts == ts_eff else Event(ev.event_id, ts_eff, ev.src, ev.dst, ev.amount, ev.type)
                ring_events.extend(g.add_edge(e2, base))
            ring = g.ring_of(ev.src) or g.ring_of(ev.dst)
            risk = int(round(100 * eff))
            forced = ("RING_MEMBER_OUT",) if side == "out" else (("RING_MEMBER_IN",) if side == "in" else ())
            reasons = self.reasons.explain(
                f,
                tier,
                forced,
                ring_id=esc_ring or (ring[0] if ring else None),
                ring_score=ring[1] if ring else None,
                risk=risk,
            )
            t3 = time.perf_counter_ns()
            timings = {"features": t1 - t0, "model": t2 - t1, "decision": t3 - t2, "total": t3 - t0}
            decision = {
                "event_id": ev.event_id,
                "decision": tier,
                "score": eff,
                "base_score": base,
                "risk": risk,
                "detector": PRODUCTION,
                "reasons": reasons,
                "ring_id": ring[0] if ring else None,
                "ring_score": round(ring[1], 6) if ring else None,
                "recommended_action": RECOMMENDED_ACTION[tier],
                "latency_us": {k: int(v // 1000) for k, v in timings.items()},
                "model_version": self.pkg.model_version,
            }
            if shadow is not None:
                decision["shadow"] = shadow
            self.lru[ev.event_id] = decision
            if len(self.lru) > self.lru_cap:
                self.lru.popitem(last=False)
            if warmup:
                self.metrics.warmup_events += 1
            else:
                self.metrics.record(timings, tier)
            self._remember(ev, decision)
            return decision, ring_events, False

    def _remember(self, ev: Event, decision: dict) -> None:
        item = {
            "event_id": ev.event_id,
            "ts": ev.ts,
            "src": ev.src,
            "dst": ev.dst,
            "amount": ev.amount,
            "type": ev.type,
            "decision": decision["decision"],
            "score": round(decision["score"], 6),
        }
        for acct in (ev.src, ev.dst):
            dq = self.acct_recent.get(acct)
            if dq is None:
                dq = deque(maxlen=20)
                self.acct_recent[acct] = dq
                if len(self.acct_recent) > 200_000:
                    self.acct_recent.popitem(last=False)
            else:
                self.acct_recent.move_to_end(acct)
            dq.append(item)

    # ------------------------------------------------------------------ views
    def account(self, acct: str) -> dict | None:
        with self.lock:
            view = self.features.account_view(acct)
            if view is None:
                return None
            ring = self.grouper.ring_of(acct)
            view["ring_id"] = ring[0] if ring else None
            view["recent_decisions"] = list(self.acct_recent.get(acct, []))[::-1]
            return view

    def rings(
        self, status: tuple[str, ...] = ("open", "alert"), limit: int = 50, detail: bool = True
    ) -> list[dict]:
        with self.lock:
            out = self.grouper.rings(status)[: max(0, int(limit))]
        if not detail:
            out = [{k: v for k, v in r.items() if k != "edges"} for r in out]
        return out

    def ring(self, ring_id: str) -> dict | None:
        with self.lock:
            return self.grouper.snapshot(ring_id)

    def engine_stats(self) -> dict:
        st = self.features.stats()
        return {
            "clock": st["clock"],
            "events": st["events"],
            "accounts": st["accounts"],
            "active_states": st["active_states"],
            "approx_state_mb": round(st["approx_bytes"] / 1e6, 1),
        }

    def info(self) -> dict:
        import platform
        import sys

        with self.lock:
            engine = self.engine_stats()
        return {
            "model": {
                "dir": str(self.model_dir.as_posix()),
                "trained_on": self.pkg.trained_on,
                "detectors": list(DETECTORS),
                "production": PRODUCTION,
                "model_version": self.pkg.model_version,
                "registry_hash": self.pkg.models["G2"].registry_hash,
                "config_hash": self.cfg.config_hash,
                "provenance": self.pkg.provenance,
                "n_trees": {d: self.pkg.models[d].n_trees for d in DETECTORS},
            },
            "platform": {
                "machine": platform.machine(),
                "system": platform.system(),
                "python": platform.python_version(),
                "cpu_count": os.cpu_count(),
                "byteorder": sys.byteorder,
            },
            "engine": engine,
            "compare_mode": self.compare,
        }

    def metrics_snapshot(self) -> dict:
        with self.lock:
            snap = self.metrics.snapshot()
            snap["rings"] = self.grouper.stats()
            snap["engine"] = self.engine_stats()
        return snap
