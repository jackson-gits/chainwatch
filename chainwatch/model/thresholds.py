"""Validation-only thresholds (spec 10.4, 11.6), the benign profile and the model-package manifest.

Base variants (B0, B1, G2): FPR quantiles of legitimate validation scores. Ring variants (+R): edge threshold,
ring-alert threshold from the false-ring budget and, when ``calibrate_with_rings``, a bisection of the base
thresholds so the false-alarm rate *after escalation* matches the target (matched FPR).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import platform
from pathlib import Path

import numpy as np

from chainwatch.config import Config, sha256_file
from chainwatch.decision.tiers import Thresholds
from chainwatch.features.matrix import load_split
from chainwatch.model.profile import build_profile
from chainwatch.model.vector_predict import VectorGBDT
from chainwatch.rings.offline import run_ring_layer

log = logging.getLogger(__name__)
DETECTORS = ("B0", "B1", "G2")


def q_higher(neg: np.ndarray, fpr: float) -> float:
    return float(np.quantile(neg, 1.0 - fpr, method="higher")) if neg.size else 1.0


def base_thresholds(neg: np.ndarray, fpr_verify: float, fpr_hold: float, fpr_edge: float) -> dict:
    verify = q_higher(neg, fpr_verify)
    hold = max(q_higher(neg, fpr_hold), verify)
    edge = q_higher(neg, fpr_edge)
    return {
        "verify": verify,
        "hold": hold,
        "edge": edge,
        "val": {
            "n_neg": int(neg.size),
            "fpr_verify": float(np.mean(neg >= verify)) if neg.size else 0.0,
            "fpr_hold": float(np.mean(neg >= hold)) if neg.size else 0.0,
        },
    }


def ring_purity(log_: dict, label_of: dict) -> dict[str, float]:
    out = {}
    for rid, lg in log_.items():
        ids = lg["event_ids"]
        if ids:
            out[rid] = sum(label_of.get(e, 0) for e in ids) / len(ids)
    return out


def choose_ring_alert(peaks_false: list[float], days: float, budget_per_day: float) -> float:
    """Smallest theta such that (false rings with peak >= theta) / days <= budget."""
    allowed = int(math.floor(budget_per_day * days + 1e-9))
    f = sorted(peaks_false, reverse=True)
    if len(f) <= allowed:
        return 0.0
    return float(np.nextafter(f[allowed], np.inf))


def _ring_alert_for(stream, scores, thr_tmp: Thresholds, cfg, label_of, days) -> tuple[float, dict]:
    run = run_ring_layer(
        stream,
        scores,
        Thresholds(thr_tmp.verify, thr_tmp.hold, thr_tmp.edge, 0.0),
        cfg.rings,
        escalation=False,
    )
    purity = ring_purity(run.log, label_of)
    fr = float(cfg.thresholds.false_ring_purity)
    peaks_false = [run.log[r]["peak"] for r, p in purity.items() if p < fr and run.log[r]["peak"] > 0]
    theta = choose_ring_alert(peaks_false, days, float(cfg.thresholds.max_false_rings_per_day))
    n_false = sum(1 for p in peaks_false if p >= theta) if theta > 0 else len(peaks_false)
    return theta, {
        "rings_tracked": len(run.log),
        "false_rings_total": len(peaks_false),
        "false_rings_per_day": n_false / days if days else 0.0,
    }


def _fpr_after(stream, scores, thr: Thresholds, cfg, y: np.ndarray) -> float:
    run = run_ring_layer(stream, scores, thr, cfg.rings)
    tier = np.asarray(run.tier)
    neg = y == 0
    return float(np.mean(tier[neg] >= 1)) if neg.any() else 0.0


def calibrate_ring_variant(stream: dict, scores: np.ndarray, y: np.ndarray, cfg: Config, days: float) -> dict:
    """Spec 11.6 on validation: edge, ring_alert and (P1) matched-FPR bisection."""
    t = cfg.thresholds
    fv, fh, fe = float(t.fpr_verify), float(t.fpr_hold), float(t.fpr_edge)
    neg = scores[y == 0]
    label_of = dict(zip(stream["event_id"].tolist(), y.tolist(), strict=False))

    def at(f_eff: float) -> tuple[Thresholds, dict]:
        b = base_thresholds(neg, f_eff, fh * f_eff / fv, fe * f_eff / fv)
        tmp = Thresholds(b["verify"], b["hold"], b["edge"], 0.0)
        theta, info = _ring_alert_for(stream, scores, tmp, cfg, label_of, days)
        return Thresholds(b["verify"], b["hold"], b["edge"], theta), {**b["val"], **info}

    thr, info = at(fv)
    fpr_before = info["fpr_verify"]
    fpr = _fpr_after(stream, scores, thr, cfg, y) if cfg.rings.escalation else fpr_before
    calib = {
        "mode": "simple",
        "iterations": 0,
        "fpr_verify_effective": fv,
        "fpr_before_escalation": fpr_before,
        "fpr_after_escalation": fpr,
    }
    if bool(t.calibrate_with_rings) and cfg.rings.escalation and fpr > fv:
        lo, hi = 0.0, fv
        best = None
        iters = 0
        for _iters in range(1, int(t.bisect_iters) + 1):
            iters = _iters
            mid = (lo + hi) / 2.0
            thr_m, info_m = at(mid)
            fpr_m = _fpr_after(stream, scores, thr_m, cfg, y)
            if fpr_m > fv:
                hi = mid
            else:
                lo = mid
                best = (mid, thr_m, info_m, fpr_m)
                if fpr_m >= 0.98 * fv:
                    break
        if best is None:
            mid = lo if lo > 0 else hi / 2 ** int(t.bisect_iters)
            thr_m, info_m = at(mid)
            best = (mid, thr_m, info_m, _fpr_after(stream, scores, thr_m, cfg, y))
        f_eff, thr, info, fpr = best
        calib = {
            "mode": "matched_fpr",
            "iterations": iters,
            "fpr_verify_effective": f_eff,
            "fpr_before_escalation": info["fpr_verify"],
            "fpr_after_escalation": fpr,
        }
    return {
        "verify": thr.verify,
        "hold": thr.hold,
        "edge": thr.edge,
        "ring_alert": thr.ring_alert,
        "val": {
            "n_neg": int(neg.size),
            "n_pos": int((y == 1).sum()),
            "fpr_verify": calib["fpr_after_escalation"],
            "fpr_hold": info["fpr_hold"],
            "false_rings_per_day": info["false_rings_per_day"],
            "rings_tracked": info["rings_tracked"],
        },
        "calibration": calib,
    }


def stream_of(split: dict) -> dict:
    return {k: split[k] for k in ("ts", "event_id", "src", "dst", "amount", "type")}


def score_split(
    models_dir: Path, split_name: str, feat_dir: Path, detectors=DETECTORS, model_names: dict | None = None
) -> tuple[dict, dict]:
    """Score one split with each detector's shipped JSON model. Returns (scores, split arrays)."""
    scores = {}
    base = load_split(feat_dir, split_name, None)
    all_names = base["names"]
    X_all = base["X"]
    for d in detectors:
        name = (model_names or {}).get(d, d)
        vm = VectorGBDT.load(models_dir / f"{name}.json")
        idx = [all_names.index(n) for n in vm.feature_names]
        scores[d] = vm.predict(np.asarray(X_all[:, idx], dtype=np.float64))
    return scores, base


def compute_thresholds(
    cfg: Config, feat_dir: str | Path, models_dir: str | Path, detectors=DETECTORS, write: bool = True
) -> dict:
    """``chainwatch thresholds``: thresholds.json, benign_profile.json and manifest.json in ``models_dir``."""
    feat_dir, models_dir = Path(feat_dir), Path(models_dir)
    meta = json.loads((feat_dir / "meta.json").read_text(encoding="utf-8"))
    scores, val = score_split(models_dir, "val", feat_dir, detectors)
    y = val["y"].astype(np.int64)
    st = stream_of(val)
    b = meta["split_ts"]
    days = (b["val_end"] - b["train_end"]) / 86400.0
    t = cfg.thresholds
    variants = {}
    for d in detectors:
        s = scores[d]
        bt = base_thresholds(s[y == 0], float(t.fpr_verify), float(t.fpr_hold), float(t.fpr_edge))
        bt["ring_alert"] = None
        bt["val"]["n_pos"] = int((y == 1).sum())
        variants[d] = bt
        log.info("thresholds %s: verify=%.4g hold=%.4g edge=%.4g", d, bt["verify"], bt["hold"], bt["edge"])
        rv = calibrate_ring_variant(st, s, y, cfg, days)
        variants[f"{d}+R"] = rv
        log.info(
            "thresholds %s+R: verify=%.4g ring_alert=%.4g fpr_after=%.4g (%s)",
            d,
            rv["verify"],
            rv["ring_alert"],
            rv["val"]["fpr_verify"],
            rv["calibration"]["mode"],
        )
    out = {
        "format": "chainwatch-thresholds-1",
        "created_from": {
            "world": meta.get("world"),
            "split": "validation",
            "config_hash": cfg.config_hash,
            "world_events_sha256": meta.get("world_events_sha256"),
            "val_days": days,
        },
        "targets": {
            "fpr_verify": float(t.fpr_verify),
            "fpr_hold": float(t.fpr_hold),
            "fpr_edge": float(t.fpr_edge),
            "max_false_rings_per_day": float(t.max_false_rings_per_day),
        },
        "variants": variants,
    }
    if write:
        with open(models_dir / "thresholds.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        g2_names = (
            json.loads((models_dir / "G2.json").read_text(encoding="utf-8"))["feature_names"]
            if (models_dir / "G2.json").exists()
            else val["names"]
        )
        idx = [val["names"].index(n) for n in g2_names]
        X_neg = np.asarray(val["X"][:, idx], dtype=np.float64)[y == 0]
        prof = build_profile(
            X_neg,
            g2_names,
            {
                "world": meta.get("world"),
                "split": "validation",
                "provenance": meta.get("provenance", "ChainWorld"),
            },
        )
        with open(models_dir / "benign_profile.json", "w", encoding="utf-8") as fh:
            json.dump(prof, fh)
        write_manifest(cfg, models_dir, meta)
    return out


def write_manifest(cfg: Config, models_dir: Path, meta: dict) -> dict:
    import lightgbm as lgb

    files = {}
    for p in sorted(models_dir.glob("*.json")):
        if p.name != "manifest.json":
            files[p.name] = sha256_file(p)
    reg = {}
    for d in DETECTORS:
        p = models_dir / f"{d}.json"
        if p.exists():
            reg[d] = json.loads(p.read_text(encoding="utf-8"))["registry_hash"]
    man = {
        "format": "chainwatch-model-package-1",
        "provenance": meta.get("provenance", "ChainWorld"),
        "trained_on": meta.get("world"),
        "config_hash": cfg.config_hash,
        "registry_hash": reg,
        "world_events_sha256": meta.get("world_events_sha256"),
        "lightgbm": lgb.__version__,
        "python": platform.python_version(),
        "created_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "files": files,
    }
    with open(models_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=1)
    return man
