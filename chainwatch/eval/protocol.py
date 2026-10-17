"""Evaluation protocol (spec 14.2): per world seed, score the test split once with every variant, write
``results/eval_<world>.json``; aggregate seeds into ``results/summary.json`` with bootstrap intervals and the
claim sentences allowed by the claims policy (wording chosen from the data).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import subprocess
from pathlib import Path

import numpy as np

from chainwatch.config import ROOT, Config, sha256_file
from chainwatch.data.io import read_accounts
from chainwatch.decision.tiers import Thresholds
from chainwatch.eval import metrics as M
from chainwatch.features.matrix import load_split
from chainwatch.model.vector_predict import VectorGBDT
from chainwatch.rings.offline import run_ring_layer

log = logging.getLogger(__name__)
RESULTS = ROOT / "results"
VARIANTS = ("RULE", "B0", "B0+R", "B1", "B1+R", "G2", "G2+R")
DETECTORS = ("B0", "B1", "G2")
TOUCH_LOG = RESULTS / "test_touch_log.jsonl"


class TestTouchRefused(RuntimeError):
    pass


def _git_commit() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
            ).stdout.strip()
            or None
        )
    except Exception:
        return None


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def model_package_hash(models_dir: Path) -> str:
    return sha256_file(Path(models_dir) / "manifest.json")


def check_touch(world_hash: str, model_hash: str, purpose: str, allow_rerun: bool) -> bool:
    """Return True if this is a rerun. Refuse a second touch of the same (world, model, purpose) unless
    allowed."""
    rerun = False
    if TOUCH_LOG.exists():
        for line in TOUCH_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if (
                rec.get("world_hash") == world_hash
                and rec.get("model_hash") == model_hash
                and rec.get("purpose", "evaluate") == purpose
            ):
                rerun = True
    if rerun and not allow_rerun:
        raise TestTouchRefused(
            f"test split already evaluated for this world and model ({purpose}); pass "
            f"--allow-rerun only for a genuine fix (it is logged and flagged)"
        )
    return rerun


def log_touch(world_hash: str, model_hash: str, purpose: str, rerun: bool, world: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    rec = {
        "world": world,
        "world_hash": world_hash,
        "model_hash": model_hash,
        "purpose": purpose,
        "code_commit": _git_commit(),
        "utc": _now(),
        "rerun": rerun,
    }
    with open(TOUCH_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------------------------- variants
def _rule_flags(split: dict) -> np.ndarray:
    names = split["names"]
    X = split["X"]
    a = np.asarray(X[:, names.index("s_matched_share")]) >= 0.7
    b = np.asarray(X[:, names.index("s_dwell_s")]) <= 900.0
    c = np.asarray(X[:, names.index("s_in_uniq_1h")]) >= 2
    return (a & b & c).astype(np.float64)


def _concat(val: dict, test: dict) -> dict:
    out = {}
    for k in (
        "ts",
        "event_id",
        "src",
        "dst",
        "amount",
        "type",
        "y",
        "ring_id",
        "hop",
        "dst_role",
        "src_role",
    ):
        out[k] = np.concatenate([val[k], test[k]])
    return out


def score_detectors(
    models_dir: Path, splits: dict, model_names: dict | None = None, detectors=DETECTORS
) -> dict:
    scores = {}
    for d in detectors:
        name = (model_names or {}).get(d, d)
        vm = VectorGBDT.load(Path(models_dir) / f"{name}.json")
        res = {}
        for sname, sp in splits.items():
            idx = [sp["names"].index(n) for n in vm.feature_names]
            res[sname] = vm.predict(np.asarray(sp["X"][:, idx], dtype=np.float64))
        scores[d] = res
    return scores


def run_variants(
    cfg: Config,
    thresholds: dict,
    val: dict,
    test: dict,
    scores: dict,
    variants=VARIANTS,
    rings_cfg: dict | None = None,
    escalation: bool | None = None,
) -> dict:
    """Final tiers and effective scores on the test rows for each variant. ``+R`` variants run the ring
    layer over validation then test (warm state); only the test part is returned."""
    n_val = val["ts"].size
    both = _concat(val, test)
    rc = dict(rings_cfg or cfg.rings)
    out = {}
    for v in variants:
        if v == "RULE":
            flags = _rule_flags(test)
            out[v] = {"tier": flags.astype(np.int64), "eff": flags, "log": {}, "escalated": 0}
            continue
        det = v.split("+")[0]
        thr_d = thresholds["variants"][v]
        thr = Thresholds.from_dict(thr_d)
        if v.endswith("+R"):
            s_all = np.concatenate([scores[det]["val"], scores[det]["test"]])
            run = run_ring_layer(both, s_all, thr, rc, use_rings=True, escalation=escalation)
            tier = np.asarray(run.tier[n_val:], dtype=np.int64)
            eff = np.asarray(run.eff[n_val:], dtype=np.float64)
            out[v] = {
                "tier": tier,
                "eff": eff,
                "log": run.log,
                "escalated": int(sum(run.escalated[n_val:])),
                "thr": thr_d,
            }
        else:
            s = scores[det]["test"]
            tier = np.where(s >= thr.hold, 2, np.where(s >= thr.verify, 1, 0)).astype(np.int64)
            out[v] = {"tier": tier, "eff": s, "log": {}, "escalated": 0, "thr": thr_d}
    return out


def evaluate_core(
    cfg: Config,
    feat_dir: Path,
    models_dir: Path,
    variants=VARIANTS,
    rings_cfg: dict | None = None,
    escalation: bool | None = None,
    thresholds: dict | None = None,
    model_names: dict | None = None,
    keep_units: bool = True,
) -> dict:
    """Compute every metric of spec 14.3 for one world and one model package (no file writes)."""
    feat_dir, models_dir = Path(feat_dir), Path(models_dir)
    meta = json.loads((feat_dir / "meta.json").read_text(encoding="utf-8"))
    world_dir = (
        ROOT / meta["world_dir"] if not Path(meta["world_dir"]).is_absolute() else Path(meta["world_dir"])
    )
    val = load_split(feat_dir, "val")
    test = load_split(feat_dir, "test")
    thresholds = thresholds or json.loads((models_dir / "thresholds.json").read_text(encoding="utf-8"))
    dets = sorted({v.split("+")[0] for v in variants if v != "RULE"})
    scores = score_detectors(models_dir, {"val": val, "test": test}, model_names, dets)
    runs = run_variants(cfg, thresholds, val, test, scores, variants, rings_cfg, escalation)
    b = meta["split_ts"]
    t_start, t_end = b["val_end"], b["test_end"]
    days = (t_end - t_start) / 86400.0
    y = test["y"].astype(np.int64)
    fpr_points = list(cfg.eval.fpr_points)
    rings_path = world_dir / "rings.json"
    rings = json.loads(rings_path.read_text(encoding="utf-8"))["rings"] if rings_path.exists() else []
    accounts = (
        read_accounts(world_dir / "accounts.csv.gz") if (world_dir / "accounts.csv.gz").exists() else []
    )
    archetype_of = {a["account_id"]: a["archetype"] for a in accounts}
    mule = {a["account_id"] for a in accounts if a["is_mule"]}
    legit_accounts = set(archetype_of) - mule
    label_of = dict(zip(test["event_id"].tolist(), y.tolist(), strict=False))
    label_of.update(zip(val["event_id"].tolist(), val["y"].astype(int).tolist(), strict=False))
    stream = {k: test[k] for k in ("ts", "src", "dst", "ring_id", "hop", "dst_role", "amount")}
    src_l, dst_l = test["src"].tolist(), test["dst"].tolist()
    stream_l = dict(stream, src=src_l, dst=dst_l)
    target_fpr = float(cfg.thresholds.fpr_verify)
    res = {
        "transaction_level": {},
        "ring_level": {},
        "archetype_fpr": {},
        "operating_points": {},
        "units": {},
    }
    for v, r in runs.items():
        tier, eff = r["tier"], r["eff"]
        tm = M.transaction_metrics(y, eff, tier, fpr_points)
        res["transaction_level"][v] = tm
        outcomes = M.ring_outcomes(
            rings,
            stream_l,
            tier,
            t_start,
            t_end,
            int(cfg.eval.catch_min_accounts),
            1 if cfg.eval.catch_min_action == "verify" else 2,
        )
        rl = M.summarize_rings(outcomes)
        rl["by_typology"] = M.breakdown(outcomes, "typology")
        rl["by_size"] = M.breakdown(outcomes, "size_bin")
        if v.endswith("+R"):
            rl["false_rings"] = M.false_rings(
                r["log"],
                label_of,
                legit_accounts,
                t_start,
                t_end,
                float(cfg.thresholds.false_ring_purity),
                days,
            )
            rl["escalated_test_txns"] = r["escalated"]
            rl["ring_peaks"] = M.ring_peaks(r["log"], label_of, t_start, t_end)
        if keep_units:
            res.setdefault("curves", {})[v] = M.curve_points(y, eff)
        res["ring_level"][v] = rl
        res["archetype_fpr"][v] = M.archetype_fpr(stream_l, y, tier, archetype_of, mule, days)
        realised = tm["deployed"]["fpr"]
        res["operating_points"][v] = {
            "thresholds": r.get("thr"),
            "target_fpr": target_fpr if v != "RULE" else None,
            "realised_test_fpr": realised,
            "fpr_off_target": (
                v != "RULE" and realised is not None and abs(realised - target_fpr) / target_fpr > 0.10
            ),
        }
        if keep_units:
            res["units"][v] = {
                "rings": [
                    {
                        k: o[k]
                        for k in (
                            "ring_id",
                            "typology",
                            "size_bin",
                            "caught",
                            "ttc_min",
                            "before_exit",
                            "has_exit",
                            "hops_remaining",
                            "min_before_exit",
                        )
                    }
                    for o in outcomes
                ],
                "blocks": M.block_counts(test["ts"], y, tier, t_start),
            }
    res["meta"] = {
        "world": meta.get("world"),
        "provenance": meta.get("provenance", "ChainWorld"),
        "world_events_sha256": meta.get("world_events_sha256"),
        "config_hash": cfg.config_hash,
        "model_hash": model_package_hash(models_dir) if (models_dir / "manifest.json").exists() else None,
        "models_dir": str(models_dir.as_posix()),
        "test_days": days,
        "test_rows": int(y.size),
        "test_positives": int(y.sum()),
        "test_rings": len([r for r in rings if t_start <= r["first_ts"] < t_end]),
        "fpr_target": target_fpr,
        "variants": list(variants),
    }
    return res


def headline(res: dict) -> dict:
    rl, tl = res["ring_level"], res["transaction_level"]
    out = {}
    for v in ("B0", "B1", "B1+R", "G2", "G2+R"):
        if v in rl:
            out[v] = {
                "ring_recall": rl[v]["recall"]["p"],
                "ttc_median_min": rl[v]["ttc_min"]["median"],
                "deployed_recall": tl[v]["deployed"]["recall"]["p"],
                "test_fpr": tl[v]["deployed"]["fpr"],
            }
    return out


def evaluate(
    cfg: Config,
    feat_dir: str | Path,
    models_dir: str | Path,
    allow_rerun: bool = False,
    out_path: str | Path | None = None,
) -> dict:
    """``chainwatch evaluate``: test-touch log, all variants, ``results/eval_<world>.json``."""
    feat_dir, models_dir = Path(feat_dir), Path(models_dir)
    meta = json.loads((feat_dir / "meta.json").read_text(encoding="utf-8"))
    world_hash = meta["world_events_sha256"]
    model_hash = model_package_hash(models_dir)
    thr = json.loads((models_dir / "thresholds.json").read_text(encoding="utf-8"))
    if thr.get("created_from", {}).get("split") != "validation":
        raise RuntimeError("thresholds.json was not fitted on validation (rule H5)")
    rerun = check_touch(world_hash, model_hash, "evaluate", allow_rerun)
    res = evaluate_core(cfg, feat_dir, models_dir)
    res["meta"]["rerun"] = rerun
    res["meta"]["code_commit"] = _git_commit()
    res["meta"]["utc"] = _now()
    res["headline"] = headline(res)
    log_touch(world_hash, model_hash, "evaluate", rerun, meta.get("world"))
    out = Path(out_path) if out_path else RESULTS / f"eval_{meta.get('world')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=_json_default)
    log.info("evaluation written to %s", out)
    return res


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


# ---------------------------------------------------------------------------------------------- worlds
def freeze_worlds(worlds: list[str] | None = None) -> dict:
    """Write SHA-256 of each world's events file to results/world_manifest.json (spec 8.2.5)."""
    base = ROOT / "data" / "processed"
    dirs = [Path(w) for w in worlds] if worlds else sorted(p for p in base.glob("world_*") if p.is_dir())
    path = RESULTS / "world_manifest.json"
    man = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"worlds": {}}
    for d in dirs:
        rep = json.loads((d / "world_report.json").read_text(encoding="utf-8"))
        man["worlds"][d.name] = {
            "events_sha256": sha256_file(d / "events.csv.gz"),
            "n_events": rep["n_events"],
            "fraud_share": rep["fraud_share"],
            "rings": rep["rings_written"],
            "config_hash": rep["config_hash"],
            "frozen_utc": _now(),
        }
    man["note"] = "Worlds are frozen: never regenerate a world listed here after G2 results have been seen."
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=1)
    return man


def check_frozen(world_dir: Path) -> bool | None:
    """True if the world matches the frozen hash, False if it differs, None if it is not frozen."""
    path = RESULTS / "world_manifest.json"
    if not path.exists():
        return None
    entry = json.loads(path.read_text(encoding="utf-8"))["worlds"].get(world_dir.name)
    if entry is None:
        return None
    return sha256_file(world_dir / "events.csv.gz") == entry["events_sha256"]


def calibrate_world(cfg: Config, preset: str, seed: int, tag: str = "calibration") -> dict:
    """Difficulty check with B0 and B1 ONLY (never G2): validation recall at 1% FPR, PR-AUC, fraud share."""
    import tempfile

    from chainwatch.data.world.generate import generate_world
    from chainwatch.features.matrix import build_matrices
    from chainwatch.model.train import train_detector

    out = {
        "preset": preset,
        "seed": seed,
        "world_params": {
            "noise_level": cfg.world.noise_level,
            "hard_negative_boost": cfg.world.hard_negative_boost,
            "mule_mix": dict(cfg.world.mule_mix),
        },
        "detectors": {},
    }
    with tempfile.TemporaryDirectory(dir=ROOT / "data" / "processed") as tmp:
        wd = Path(tmp) / f"world_{preset}_seed{seed}"
        rep = generate_world(cfg, seed, wd)
        fd = Path(tmp) / f"features_{wd.name}"
        build_matrices(cfg, wd, fd)
        md = Path(tmp) / "models"
        for d in ("B0", "B1"):
            r = train_detector(cfg, fd, d, md, seed=seed)
            va = load_split(fd, "val", r["feature_names"])
            s = VectorGBDT.load(md / f"{d}.json").predict(np.asarray(va["X"]))
            yv = va["y"].astype(int)
            out["detectors"][d] = {
                "pr_auc_val": M.average_precision(yv, s),
                "recall_at_1pct_fpr_val": M.recall_at_fpr_curve(yv, s, 0.01)["recall"],
                "recall_at_0.5pct_fpr_val": M.recall_at_fpr_curve(yv, s, 0.005)["recall"],
            }
        out["fraud_share"] = rep["fraud_share"]
        out["fraud_share_in_band"] = rep["fraud_share_in_band"]
        out["n_events"] = rep["n_events"]
    b1 = out["detectors"]["B1"]["recall_at_1pct_fpr_val"]
    out["b1_in_target_band"] = b1 is not None and 0.4 <= b1 <= 0.9
    path = RESULTS / f"calibration_{tag}.json"
    RESULTS.mkdir(parents=True, exist_ok=True)
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": []}
    out["utc"] = _now()
    hist["runs"].append(out)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, indent=1)
    return out


# ---------------------------------------------------------------------------------------------- eval-all
def run_seed(cfg_dict: dict, preset: str, seed: int, skip_existing: bool, allow_rerun: bool) -> str:
    """One seed end to end (used in worker processes)."""
    from chainwatch.config import Config, _to_section
    from chainwatch.data.world.generate import generate_world, world_tag
    from chainwatch.features.matrix import build_matrices
    from chainwatch.model.thresholds import compute_thresholds
    from chainwatch.model.train import train_detector

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [seed {seed}] %(name)s: %(message)s")
    cfg = Config(_to_section(cfg_dict))
    base = ROOT / "data" / "processed"
    wd = base / world_tag(preset, seed)
    frozen = check_frozen(wd) if (wd / "events.csv.gz").exists() else None
    if not (wd / "events.csv.gz").exists():
        if (RESULTS / "world_manifest.json").exists() and world_tag(preset, seed) in json.loads(
            (RESULTS / "world_manifest.json").read_text()
        )["worlds"]:
            log.info("regenerating frozen world %s (hash will be verified)", wd.name)
        generate_world(cfg, seed, wd)
        frozen = check_frozen(wd)
    if frozen is False:
        raise RuntimeError(f"{wd.name} differs from its frozen hash in results/world_manifest.json")
    fd = base / f"features_{wd.name}"
    if not (skip_existing and (fd / "meta.json").exists()):
        build_matrices(cfg, wd, fd)
    md = ROOT / "models" / "eval" / f"{preset}_seed{seed}"
    if not (skip_existing and (md / "manifest.json").exists()):
        for d in DETECTORS:
            train_detector(cfg, fd, d, md, seed=seed)
        compute_thresholds(cfg, fd, md)
    out = RESULTS / f"eval_{wd.name}.json"
    if skip_existing and out.exists():
        return str(out)
    evaluate(cfg, fd, md, allow_rerun=allow_rerun, out_path=out)
    return str(out)


def eval_all(
    cfg: Config,
    preset: str,
    seeds: list[int],
    skip_existing: bool = False,
    jobs: int = 1,
    allow_rerun: bool = False,
) -> dict:
    paths = []
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(run_seed, cfg.to_dict(), preset, s, skip_existing, allow_rerun) for s in seeds]
            paths = [f.result() for f in futs]
    else:
        paths = [run_seed(cfg.to_dict(), preset, s, skip_existing, allow_rerun) for s in seeds]
    summary = aggregate(cfg, [Path(p) for p in paths])
    with open(RESULTS / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, default=_json_default)
    return summary


# ---------------------------------------------------------------------------------------------- aggregate
def _vals(evals: list[dict], getter) -> list:
    out = []
    for e in evals:
        try:
            v = getter(e)
        except (KeyError, TypeError):
            v = None
        out.append(v)
    return out


def _mmm(values: list) -> dict:
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return {"mean": None, "min": None, "max": None, "per_seed": values}
    return {
        "mean": float(np.mean(vals)),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "per_seed": values,
    }


def _direction(ci: list, more: str, similar: str, fewer: str) -> str:
    lo, hi = ci
    if lo is None:
        return similar
    if lo > 0:
        return more
    if hi < 0:
        return fewer
    return similar


def aggregate(cfg: Config, paths: list[Path]) -> dict:
    """Seeds -> summary: per-variant mean/min/max, pooled and paired bootstrap intervals, claim sentences."""
    evals = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    n_boot = int(cfg.eval.bootstrap)
    seed = int(cfg.eval.get("bootstrap_seed", 2026))
    variants = [v for v in VARIANTS if all(v in e["ring_level"] for e in evals)]
    summ: dict = {
        "provenance": "ChainWorld",
        "n_worlds": len(evals),
        "worlds": [e["meta"]["world"] for e in evals],
        "fpr_target": float(cfg.thresholds.fpr_verify),
        "variants": {},
        "paired": {},
        "claim": {},
    }
    for v in variants:
        summ["variants"][v] = {
            "ring_recall": _mmm(_vals(evals, lambda e: e["ring_level"][v]["recall"]["p"])),
            "ttc_median_min": _mmm(_vals(evals, lambda e: e["ring_level"][v]["ttc_min"]["median"])),
            "caught_before_exit": _mmm(_vals(evals, lambda e: e["ring_level"][v]["caught_before_exit"]["p"])),
            "hops_remaining_median": _mmm(
                _vals(evals, lambda e: e["ring_level"][v]["hops_remaining"]["median"])
            ),
            "deployed_recall": _mmm(
                _vals(evals, lambda e: e["transaction_level"][v]["deployed"]["recall"]["p"])
            ),
            "test_fpr": _mmm(_vals(evals, lambda e: e["transaction_level"][v]["deployed"]["fpr"])),
            "pr_auc": _mmm(_vals(evals, lambda e: e["transaction_level"][v]["pr_auc"])),
            "roc_auc": _mmm(_vals(evals, lambda e: e["transaction_level"][v]["roc_auc"])),
            "within_1h": _mmm(_vals(evals, lambda e: e["ring_level"][v]["within_1h"]["p"])),
            "prevented_upper_mean": _mmm(_vals(evals, lambda e: e["ring_level"][v]["prevented_upper_mean"])),
            "prevented_lower_mean": _mmm(_vals(evals, lambda e: e["ring_level"][v]["prevented_lower_mean"])),
            "false_rings_per_day": _mmm(
                _vals(evals, lambda e: e["ring_level"][v]["false_rings"]["false_per_day"])
            )
            if v.endswith("+R")
            else None,
            "fpr_off_target_any": any(e["operating_points"][v]["fpr_off_target"] for e in evals),
        }
        # pooled rings over seeds
        units = [(i, u) for i, e in enumerate(evals) for u in e["units"][v]["rings"]]
        caught = np.array([1.0 if u["caught"] else 0.0 for _, u in units])
        ttc = np.array([u["ttc_min"] for _, u in units if u["caught"]], dtype=float)
        ex = np.array([1.0 if u["before_exit"] else 0.0 for _, u in units if u["has_exit"]])
        hops = np.array(
            [u["hops_remaining"] for _, u in units if u["hops_remaining"] is not None], dtype=float
        )
        pooled = summ["variants"][v]["pooled"] = {
            "n_rings": int(caught.size),
            "n_caught": int(caught.sum()),
            "ring_recall": float(caught.mean()) if caught.size else None,
            "ring_recall_ci": M.bootstrap_ci(caught, np.mean, n_boot, seed),
            "ttc_median_min": float(np.median(ttc)) if ttc.size else None,
            "ttc_median_ci": M.bootstrap_ci(ttc, np.median, n_boot, seed),
            "before_exit": float(ex.mean()) if ex.size else None,
            "n_with_exit": int(ex.size),
            "before_exit_ci": M.bootstrap_ci(ex, np.mean, n_boot, seed),
            "hops_remaining_median": float(np.median(hops)) if hops.size else None,
            "hops_remaining_mean": float(np.mean(hops)) if hops.size else None,
        }
        blocks = [e["units"][v]["blocks"] for e in evals]
        pos = np.concatenate([np.array(b["pos"]) for b in blocks])
        hit = np.concatenate([np.array(b["pos_hit"]) for b in blocks])
        arr = np.stack([pos, hit], axis=1) if pos.size else np.zeros((0, 2))
        pooled["deployed_recall"] = float(hit.sum() / pos.sum()) if pos.sum() else None
        pooled["deployed_recall_ci"] = M.bootstrap_ci(
            arr, lambda a: a[:, 1].sum() / a[:, 0].sum() if a[:, 0].sum() else np.nan, n_boot, seed
        )
        # by typology (pooled)
        by_typ: dict = {}
        for _, u in units:
            by_typ.setdefault(u["typology"], []).append(1.0 if u["caught"] else 0.0)
        pooled["recall_by_typology"] = {
            t: {
                "recall": float(np.mean(x)),
                "n": len(x),
                "ci": M.bootstrap_ci(np.array(x), np.mean, n_boot, seed),
            }
            for t, x in sorted(by_typ.items())
        }

    # paired differences: G2+R minus each other variant, same rings and blocks
    if "G2+R" in variants:
        ref_units = [u for e in evals for u in e["units"]["G2+R"]["rings"]]
        ref_blocks = [e["units"]["G2+R"]["blocks"] for e in evals]
        for v in variants:
            if v == "G2+R":
                continue
            other = [u for e in evals for u in e["units"][v]["rings"]]
            a = np.array([1.0 if u["caught"] else 0.0 for u in ref_units])
            b = np.array([1.0 if u["caught"] else 0.0 for u in other])
            d = a - b
            both = [
                (x["ttc_min"], y["ttc_min"])
                for x, y in zip(ref_units, other, strict=False)
                if x["caught"] and y["caught"]
            ]
            dt = np.array([x - y for x, y in both], dtype=float)
            ex_pairs = [
                (1.0 if x["before_exit"] else 0.0) - (1.0 if y["before_exit"] else 0.0)
                for x, y in zip(ref_units, other, strict=False)
                if x["has_exit"]
            ]
            ob = [e["units"][v]["blocks"] for e in evals]
            pa = np.concatenate([np.array(x["pos"]) for x in ref_blocks])
            ha = np.concatenate([np.array(x["pos_hit"]) for x in ref_blocks])
            hb = np.concatenate([np.array(x["pos_hit"]) for x in ob])
            blk = np.stack([pa, ha, hb], axis=1) if pa.size else np.zeros((0, 3))
            summ["paired"][f"G2+R_minus_{v}"] = {
                "ring_recall_diff": float(d.mean()) if d.size else None,
                "ring_recall_diff_ci": M.bootstrap_ci(d, np.mean, n_boot, seed),
                "discordant_g2r_only": int(np.sum((a == 1) & (b == 0))),
                "discordant_other_only": int(np.sum((a == 0) & (b == 1))),
                "n_rings": int(d.size),
                "ttc_diff_min_median": float(np.median(dt)) if dt.size else None,
                "ttc_diff_min_ci": M.bootstrap_ci(dt, np.median, n_boot, seed),
                "n_caught_by_both": int(dt.size),
                "before_exit_diff": float(np.mean(ex_pairs)) if ex_pairs else None,
                "before_exit_diff_ci": M.bootstrap_ci(np.array(ex_pairs), np.mean, n_boot, seed),
                "deployed_recall_diff": float((ha.sum() - hb.sum()) / pa.sum()) if pa.sum() else None,
                "deployed_recall_diff_ci": M.bootstrap_ci(
                    blk,
                    lambda x: (x[:, 1].sum() - x[:, 2].sum()) / x[:, 0].sum() if x[:, 0].sum() else np.nan,
                    n_boot,
                    seed,
                ),
            }
    summ["claim"] = claim_sentences(summ)
    summ["utc"] = _now()
    return summ


def _pct(x) -> str:
    return "n/a" if x is None else f"{round(100 * x)}%"


def claim_sentences(summ: dict) -> dict:
    """Sentences whose wording follows the paired intervals (spec 20.3). Never edited by hand."""
    V, P = summ["variants"], summ["paired"]
    out = {}
    if "G2+R" not in V:
        return out
    n = summ["n_worlds"]
    fpr = summ["fpr_target"]
    g = V["G2+R"]["pooled"]
    parts = []
    for v, label in (
        ("B0", "the one-payment-at-a-time model (B0)"),
        ("B1+R", "the account-history baseline with ring grouping (B1+R)"),
    ):
        if v in V:
            parts.append(f"{_pct(V[v]['pooled']['ring_recall'])} for {label}")
    pb0 = P.get("G2+R_minus_B0", {})
    pb1 = P.get("G2+R_minus_B1+R", {})
    verb = _direction(
        pb0.get("ring_recall_diff_ci", [None, None]),
        "found more rings than",
        "found a similar share of rings as",
        "found fewer rings than",
    )
    off = [v for v in ("B0", "B1+R", "G2+R") if v in V and V[v]["fpr_off_target_any"]]
    fpr_note = ""
    if off:
        fpr_note = (
            " Realised test false-alarm rates differed from the target by more than 10% for "
            + ", ".join(off)
            + " in at least one world (see the operating-point table)."
        )
    out["headline"] = (
        f"On our synthetic benchmark ({n} worlds, time-based split), at the same false-alarm rate "
        f"({fpr * 100:.1f}% of legitimate transactions, thresholds fixed on validation), "
        f"the ring-aware detector (G2+R) found {_pct(g['ring_recall'])} of mule rings, versus "
        + " and ".join(parts)
        + "; it "
        f"{verb} the one-payment-at-a-time model (paired 95% interval of the difference "
        f"{_ci_words(pb0.get('ring_recall_diff_ci'))})." + fpr_note
    )
    if pb1:
        verb1 = _direction(
            pb1.get("ring_recall_diff_ci", [None, None]),
            "found more rings than",
            "found a similar share of rings as",
            "found fewer rings than",
        )
        out["vs_strong_baseline"] = (
            f"Against the strong account-history baseline with the same ring layer (B1+R), G2+R {verb1} it "
            f"({_pct(g['ring_recall'])} versus {_pct(V['B1+R']['pooled']['ring_recall'])}; "
            f"paired 95% interval of the difference {_ci_words(pb1.get('ring_recall_diff_ci'))}; "
            f"{pb1.get('discordant_g2r_only')} rings caught only by G2+R and "
            f"{pb1.get('discordant_other_only')} only by B1+R)."
        )
        dt = pb1.get("ttc_diff_min_median")
        if dt is not None:
            when = _direction(
                [-(x) if x is not None else None for x in reversed(pb1["ttc_diff_min_ci"])],
                "earlier",
                "at a similar time",
                "later",
            )
            out["earliness"] = (
                f"Among the {pb1['n_caught_by_both']} rings caught by both, G2+R caught them {when} "
                f"than B1+R (median paired difference {dt:+.1f} minutes; 95% interval "
                f"{_ci_words(pb1['ttc_diff_min_ci'], unit=' min', pct=False)})."
            )
    ex = g.get("before_exit")
    if ex is not None and "B0" in V:
        out["before_exit"] = (
            f"G2+R caught {_pct(ex)} of rings before their first cash-out (of {g['n_with_exit']} rings "
            f"with a cash-out), versus {_pct(V['B0']['pooled']['before_exit'])} for B0."
        )
    if g.get("hops_remaining_median") is not None:
        out["hops"] = (
            f"At the moment G2+R caught a ring, a median of {g['hops_remaining_median']:.0f} hop(s) "
            f"remained before the first cash-out."
        )
    typ = g.get("recall_by_typology", {})
    if typ:
        worst = min(typ.items(), key=lambda kv: kv[1]["recall"])
        out["weakest_typology"] = (
            f"The weakest typology for G2+R was {worst[0].replace('_', ' ')} "
            f"({_pct(worst[1]['recall'])} of {worst[1]['n']} rings caught)."
        )
    return out


def _ci_words(ci, unit: str = "", pct: bool = True) -> str:
    if not ci or ci[0] is None:
        return "not available"
    lo, hi = ci
    f = (lambda x: f"{x * 100:+.0f} points") if pct else (lambda x: f"{x:+.1f}{unit}")
    side = "excludes zero" if (lo > 0 or hi < 0) else "includes zero"
    return f"{f(lo)} to {f(hi)}, {side}"


def resolve_seed_paths(preset: str, seeds: list[int]) -> list[Path]:
    from chainwatch.data.world.generate import world_tag

    return [RESULTS / f"eval_{world_tag(preset, s)}.json" for s in seeds]


def cpu_jobs(default: int = 1) -> int:
    return max(1, min(default, (os.cpu_count() or 2) // 4))
