"""Cross-platform decision parity (spec 14.8). Pure Python: runs in the serving image on any architecture.

``write_parity`` warms the engine on the demo world, then writes one JSON line per event for a fixed slice:
``event_id``, ``decision``, ``score`` and ``base_score`` (Python ``repr``) and ``ring_id``.
``compare_parity`` reports lines compared, differing decisions, the maximum score difference and the first
differing line.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

from chainwatch.config import Config, resolve_path
from chainwatch.data.io import iter_events


def write_parity(
    cfg: Config, out: str | Path, model_dir: str | Path | None = None, n_events: int = 20000
) -> dict:
    from chainwatch.serving.engine import ScoringEngine

    t0 = time.time()
    eng = ScoringEngine(cfg, model_dir, compare=False)
    warm = float(cfg.replay.warmup_until_ts)
    out = resolve_path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    n_warm = 0
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            json.dumps(
                {
                    "meta": {
                        "machine": platform.machine(),
                        "byteorder": sys.byteorder,
                        "python": platform.python_version(),
                        "model_version": eng.pkg.model_version,
                        "dataset": str(cfg.replay.dataset),
                        "n_events": n_events,
                        "warmup_until_ts": warm,
                    }
                }
            )
            + "\n"
        )
        for le in iter_events(resolve_path(cfg.replay.dataset)):
            ev = le.event
            if ev.ts < warm:
                eng.process(ev, warmup=True)
                n_warm += 1
                continue
            d, _, _ = eng.process(ev)
            fh.write(
                json.dumps(
                    {
                        "event_id": ev.event_id,
                        "decision": d["decision"],
                        "score": repr(d["score"]),
                        "base_score": repr(d["base_score"]),
                        "ring_id": d["ring_id"],
                    }
                )
                + "\n"
            )
            n += 1
            if n >= n_events:
                break
    return {
        "out": str(out),
        "lines": n,
        "warmup_events": n_warm,
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "seconds": round(time.time() - t0, 1),
    }


def _read(path: str | Path) -> tuple[dict, list[dict]]:
    meta, rows = {}, []
    with open(resolve_path(path), encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if "meta" in rec:
                meta = rec["meta"]
            else:
                rows.append(rec)
    return meta, rows


def compare_parity(a: str | Path, b: str | Path) -> dict:
    ma, ra = _read(a)
    mb, rb = _read(b)
    n = min(len(ra), len(rb))
    mism = 0
    ring_mism = 0
    max_diff = 0.0
    first = None
    for i in range(n):
        x, y = ra[i], rb[i]
        if x["event_id"] != y["event_id"]:
            first = first or {"line": i, "reason": "event_id differs", "a": x, "b": y}
            mism += 1
            continue
        diff = max(
            abs(float(x["score"]) - float(y["score"])), abs(float(x["base_score"]) - float(y["base_score"]))
        )
        max_diff = max(max_diff, diff)
        if x["decision"] != y["decision"]:
            mism += 1
            first = first or {"line": i, "reason": "decision differs", "a": x, "b": y}
        if x["ring_id"] != y["ring_id"]:
            ring_mism += 1
        if diff > 0 and first is None:
            first = {"line": i, "reason": "score differs", "a": x, "b": y}
    return {
        "a": {"file": str(a), **ma},
        "b": {"file": str(b), **mb},
        "lines_compared": n,
        "decision_mismatches": mism,
        "ring_id_mismatches": ring_mism,
        "max_abs_score_diff": max_diff,
        "identical_scores": max_diff == 0.0,
        "first_difference": first,
        "length_a": len(ra),
        "length_b": len(rb),
    }
