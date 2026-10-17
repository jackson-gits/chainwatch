"""Ablations A1 to A10 (spec 14.5). Results in ``results/ablation_<world>.json`` and
``results/ablation_summary.json``.

A1/A2 come from the main evaluation. A3 to A5 and A10 retrain (same hyperparameters and seeds). A6 to A9
reuse the scores and change only the ring layer; for each, the ring-alert threshold and the matched-FPR
calibration are redone on validation so comparisons stay at matched FPR.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path

import numpy as np

from chainwatch.config import ROOT, Config
from chainwatch.data.world.generate import world_tag
from chainwatch.eval.protocol import (
    RESULTS,
    _json_default,
    check_touch,
    evaluate_core,
    log_touch,
    model_package_hash,
)

log = logging.getLogger(__name__)

NEIGHBOUR = ["g_nbr_in_susp_cnt", "g_nbr_out_susp_cnt", "g_dst_nbr_in_susp_cnt", "g_src_susp", "g_dst_susp"]
TRAIL = ["g_trail_depth", "g_funnel_in"]
FLOW = ["g_flow_comp_size", "g_flow_comp_edges"]
RETRAIN = {"A3": NEIGHBOUR, "A4": TRAIL, "A5": FLOW}
ALL_IDS = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10")


def _short(res: dict, variants) -> dict:
    out = {}
    for v in variants:
        rl, tl = res["ring_level"][v], res["transaction_level"][v]
        out[v] = {
            "ring_recall": rl["recall"]["p"],
            "n_rings": rl["n_rings"],
            "ttc_median_min": rl["ttc_min"]["median"],
            "caught_before_exit": rl["caught_before_exit"]["p"],
            "hops_remaining_median": rl["hops_remaining"]["median"],
            "deployed_recall": tl["deployed"]["recall"]["p"],
            "test_fpr": tl["deployed"]["fpr"],
            "pr_auc": tl["pr_auc"],
            "false_rings_per_day": rl.get("false_rings", {}).get("false_per_day"),
        }
    return out


def _thresholds_for(cfg: Config, feat: Path, pkg_dir: Path, rings_override: dict | None) -> dict:
    """G2 and G2+R thresholds for a (possibly ablated) package, with an optional ring-config override."""
    from chainwatch.model.thresholds import (
        base_thresholds,
        calibrate_ring_variant,
        score_split,
        stream_of,
    )

    c2 = cfg.with_overrides({f"rings.{k}": v for k, v in (rings_override or {}).items()})
    meta = json.loads((feat / "meta.json").read_text(encoding="utf-8"))
    scores, val = score_split(pkg_dir, "val", feat, ("G2",))
    y = val["y"].astype(np.int64)
    b = meta["split_ts"]
    days = (b["val_end"] - b["train_end"]) / 86400.0
    t = c2.thresholds
    base = base_thresholds(scores["G2"][y == 0], float(t.fpr_verify), float(t.fpr_hold), float(t.fpr_edge))
    ring = calibrate_ring_variant(stream_of(val), scores["G2"], y, c2, days)
    return {
        "format": "chainwatch-thresholds-1",
        "created_from": {"split": "validation"},
        "variants": {"G2": base, "G2+R": ring},
    }, c2


def run_seed_ablations(cfg_dict: dict, preset: str, seed: int, ids: list[str]) -> dict:
    from chainwatch.config import Config, _to_section
    from chainwatch.model.train import train_detector

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [abl seed {seed}] %(name)s: %(message)s")
    cfg = Config(_to_section(cfg_dict))
    wt = world_tag(preset, seed)
    feat = ROOT / "data" / "processed" / f"features_{wt}"
    pkg = ROOT / "models" / "eval" / f"{preset}_seed{seed}"
    main_eval = RESULTS / f"eval_{wt}.json"
    meta = json.loads((feat / "meta.json").read_text(encoding="utf-8"))
    out: dict = {"world": wt, "provenance": "ChainWorld", "ablations": {}}
    if main_eval.exists():
        ev = json.loads(main_eval.read_text(encoding="utf-8"))
        if "A1" in ids:
            out["ablations"]["A1"] = {
                "change": "detector ladder without rings",
                "results": _short(ev, ("B0", "B1", "G2")),
            }
        if "A2" in ids:
            out["ablations"]["A2"] = {
                "change": "detector ladder with rings",
                "results": _short(ev, ("B0+R", "B1+R", "G2+R")),
            }
    world_hash = meta["world_events_sha256"]

    def touch(purpose: str, pkg_dir: Path) -> None:
        mh = model_package_hash(pkg_dir) if (pkg_dir / "manifest.json").exists() else "none"
        rerun = check_touch(world_hash, mh + purpose, purpose, allow_rerun=True)
        log_touch(world_hash, mh + purpose, purpose, rerun, wt)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # ---------------- retraining ablations ----------------
        for aid, drop in RETRAIN.items():
            if aid not in ids:
                continue
            d = tmp / aid
            d.mkdir()
            for f in ("B0.json", "B1.json", "manifest.json"):
                shutil.copy(pkg / f, d / f)
            rep = train_detector(cfg, feat, "G2", d, seed=seed, extra_disabled=drop)
            thr, c2 = _thresholds_for(cfg, feat, d, None)
            res = evaluate_core(cfg, feat, d, variants=("G2", "G2+R"), thresholds=thr, keep_units=False)
            touch(f"ablation:{aid}", d)
            out["ablations"][aid] = {
                "change": f"G2 without {', '.join(drop)} (retrained)",
                "dropped": drop,
                "val_pr_auc": rep["pr_auc_val_shipped"],
                "results": _short(res, ("G2", "G2+R")),
            }
        if "A10" in ids:
            d = tmp / "A10"
            d.mkdir()
            c0 = cfg.with_overrides({"features.priority": "P0"})
            res_names = {}
            for det in ("B1", "G2"):
                rep = train_detector(c0, feat, det, d, seed=seed)
                res_names[det] = rep["n_features"]
            shutil.copy(pkg / "B0.json", d / "B0.json")
            shutil.copy(pkg / "manifest.json", d / "manifest.json")
            thr, _ = _thresholds_for(c0, feat, d, None)
            res = evaluate_core(c0, feat, d, variants=("G2", "G2+R"), thresholds=thr, keep_units=False)
            touch("ablation:A10", d)
            out["ablations"]["A10"] = {
                "change": "P0 feature set (27) instead of P1 (59), retrained",
                "n_features": res_names,
                "results": _short(res, ("G2", "G2+R")),
            }
        # ---------------- ring-layer ablations (scores reused) ----------------
        ring_cases = []
        if "A6" in ids:
            ring_cases.append(("A6", "require_pass_node = false", {"require_pass_node": False}))
        if "A7" in ids:
            ring_cases.append(("A7", "escalation = false", {"escalation": False}))
        if "A8" in ids:
            lw = float(cfg.rings.link_window_s)
            ring_cases.append(("A8_x0.5", "link_window_s x 0.5", {"link_window_s": lw * 0.5}))
            ring_cases.append(("A8_x2", "link_window_s x 2", {"link_window_s": lw * 2.0}))
        if "A9" in ids:
            for k in (1, 3, 5):
                ring_cases.append((f"A9_k{k}", f"top_k_noisy_or = {k}", {"top_k_noisy_or": k}))
        for aid, change, override in ring_cases:
            thr, c2 = _thresholds_for(cfg, feat, pkg, override)
            res = evaluate_core(c2, feat, pkg, variants=("G2+R",), thresholds=thr, keep_units=False)
            touch(f"ablation:{aid}", pkg)
            out["ablations"][aid] = {
                "change": change,
                "override": override,
                "results": _short(res, ("G2+R",)),
            }
    path = RESULTS / f"ablation_{wt}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=_json_default)
    return out


def run_ablations(
    cfg: Config, preset: str, seeds: list[int], ids: list[str] | None = None, jobs: int = 1
) -> dict:
    ids = list(ids or ALL_IDS)
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=jobs) as ex:
            per = list(
                ex.map(
                    run_seed_ablations,
                    [cfg.to_dict()] * len(seeds),
                    [preset] * len(seeds),
                    seeds,
                    [ids] * len(seeds),
                )
            )
    else:
        per = [run_seed_ablations(cfg.to_dict(), preset, s, ids) for s in seeds]
    summary: dict = {"provenance": "ChainWorld", "worlds": [p["world"] for p in per], "ablations": {}}
    keys = sorted({k for p in per for k in p["ablations"]})
    for k in keys:
        rows = [p["ablations"][k] for p in per if k in p["ablations"]]
        variants = sorted({v for r in rows for v in r["results"]})
        agg = {}
        for v in variants:
            agg[v] = {}
            for m in (
                "ring_recall",
                "ttc_median_min",
                "caught_before_exit",
                "deployed_recall",
                "test_fpr",
                "pr_auc",
                "false_rings_per_day",
                "hops_remaining_median",
            ):
                vals = [
                    r["results"][v][m] for r in rows if v in r["results"] and r["results"][v][m] is not None
                ]
                agg[v][m] = {
                    "mean": float(np.mean(vals)) if vals else None,
                    "min": float(np.min(vals)) if vals else None,
                    "max": float(np.max(vals)) if vals else None,
                    "n_worlds": len(vals),
                }
        summary["ablations"][k] = {"change": rows[0]["change"], "results": agg}
    with open(RESULTS / "ablation_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, default=_json_default)
    return {"summary": summary, "per_world": per}
