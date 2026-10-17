"""Leakage checks L1 to L9 (spec 14.7). Results go to ``results/leakage_<world>.json`` and into the report.

A failure of L1, L7, L8 or L9 blocks the release. A failure of L2 or L3 means a bug.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import random
import tempfile
from pathlib import Path

import numpy as np

from chainwatch.config import ROOT, Config
from chainwatch.eval import metrics as M
from chainwatch.features.matrix import load_split
from chainwatch.features.registry import BY_NAME

log = logging.getLogger(__name__)
BLOCKING = ("L1", "L7", "L8", "L9")
BALANCE_FEATURES = ("t_s_drain", "t_s_bal_after_zero", "t_s_bal_known")


def _world_dir(meta: dict) -> Path:
    p = Path(meta["world_dir"])
    return p if p.is_absolute() else ROOT / p


def l1_temporal(feat_dir: Path, meta: dict, cfg: Config, n_check: int = 3000) -> dict:
    tr, va, te = (np.load(feat_dir / f"{s}_ts.npy") for s in ("train", "val", "test"))
    ok_order = bool(
        (tr.max() if tr.size else -1) < (va.min() if va.size else np.inf)
        and (va.max() if va.size else -1) < (te.min() if te.size else np.inf)
    )
    causal = causality_spot_check(cfg, _world_dir(meta) / "events.csv.gz", n_check)
    return {
        "pass": ok_order and causal["pass"],
        "max_train_ts": float(tr.max()) if tr.size else None,
        "min_val_ts": float(va.min()) if va.size else None,
        "max_val_ts": float(va.max()) if va.size else None,
        "min_test_ts": float(te.min()) if te.size else None,
        "causality": causal,
    }


def causality_spot_check(cfg: Config, events_path: Path, n: int = 3000, cuts=(500, 1500, 2500)) -> dict:
    """Features of events 0..i on the prefix equal those on the full stream; changing later events
    changes nothing."""
    from chainwatch.data.io import iter_events
    from chainwatch.features.engine import FeatureEngine
    from chainwatch.schema import Event

    evs = []
    for le in iter_events(events_path):
        evs.append(le.event)
        if len(evs) >= n:
            break
    eng = FeatureEngine(cfg, ("G2",))
    full = [eng.step(e) for e in evs]
    problems = 0
    for cut in cuts:
        if cut >= len(evs):
            continue
        e2 = FeatureEngine(cfg, ("G2",))
        mutated = [Event(e.event_id, e.ts, e.dst, e.src, e.amount * 3.0, e.type) for e in evs[cut:]]
        part = [e2.step(e) for e in evs[:cut] + mutated]
        for i in range(cut):
            if part[i] != full[i]:
                problems += 1
                break
    return {"pass": problems == 0, "events": len(evs), "cuts": [c for c in cuts if c < len(evs)]}


def _id_features(ids: np.ndarray) -> np.ndarray:
    num = np.array(
        [int("".join(ch for ch in s if ch.isdigit()) or 0) % 10_000_000 for s in ids], dtype=np.float64
    )
    length = np.array([len(s) for s in ids], dtype=np.float64)
    first = np.array([ord(s[0]) if s else 0 for s in ids], dtype=np.float64)
    second = np.array([ord(s[1]) if len(s) > 1 else 0 for s in ids], dtype=np.float64)
    last = np.array([ord(s[-1]) if s else 0 for s in ids], dtype=np.float64)
    return np.stack([num, length, first, second, last], axis=1)


def l2_id_only(feat_dir: Path, cfg: Config) -> dict:
    import lightgbm as lgb

    tr = load_split(feat_dir, "train", [])
    va = load_split(feat_dir, "val", [])
    Xtr = np.hstack([_id_features(tr["src"]), _id_features(tr["dst"])])
    Xva = np.hstack([_id_features(va["src"]), _id_features(va["dst"])])
    ytr, yva = tr["y"].astype(int), va["y"].astype(int)
    train_ids = set(np.concatenate([tr["src"], tr["dst"]]).tolist())
    val_src_seen = np.fromiter((x in train_ids for x in va["src"]), dtype=bool, count=len(yva))
    val_dst_seen = np.fromiter((x in train_ids for x in va["dst"]), dtype=bool, count=len(yva))
    params = {
        "objective": "binary",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 50,
        "seed": int(cfg.project.seed),
        "deterministic": True,
        "force_row_wise": True,
        "verbosity": -1,
        "num_threads": int(cfg.model.n_jobs),
    }
    b = lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=200)
    s = b.predict(Xva)
    ap = M.average_precision(yva, s)
    base = float(yva.mean())
    # Preserve repeated identities within validation but remap them to unseen random IDs. This
    # separates memorization of an account across the temporal split from numeric ID structure.
    rng = random.Random(int(cfg.project.seed) + 41)
    train_id_values = set(np.concatenate([tr["src"], tr["dst"]]).tolist())
    val_ids = set(np.concatenate([va["src"], va["dst"]]).tolist())
    remapped = {}
    used_remapped = set(train_id_values)
    for account_id in sorted(val_ids):
        candidate = ""
        while not candidate or candidate in used_remapped:
            candidate = f"A{rng.randrange(100_000_000):08d}"
        remapped[account_id] = candidate
        used_remapped.add(candidate)
    remap_src = np.asarray([remapped[x] for x in va["src"]])
    remap_dst = np.asarray([remapped[x] for x in va["dst"]])
    Xva_remapped = np.hstack([_id_features(remap_src), _id_features(remap_dst)])
    remapped_ap = M.average_precision(yva, b.predict(Xva_remapped))
    meta = json.loads((feat_dir / "meta.json").read_text(encoding="utf-8"))
    assignment_auc = None
    accounts = _world_dir(meta) / "accounts.csv.gz"
    if accounts.exists():
        with gzip.open(accounts, "rt", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if rows:
            ids = np.asarray([_id_features(np.asarray([r["account_id"]]))[0, 0] for r in rows])
            labels = np.asarray([int(r["is_mule"]) for r in rows])
            auc = M.roc_auc(labels, ids)
            assignment_auc = max(auc, 1.0 - auc) if np.isfinite(auc) else None
    return {
        "pass": bool(ap <= 2.0 * base),
        "pr_auc": ap,
        "positive_rate": base,
        "bound": 2.0 * base,
        "train_unique_accounts": len(train_ids),
        "val_events_with_seen_src": float(val_src_seen.mean()) if val_src_seen.size else None,
        "val_events_with_seen_dst": float(val_dst_seen.mean()) if val_dst_seen.size else None,
        "val_events_with_both_seen": float((val_src_seen & val_dst_seen).mean()) if yva.size else None,
        "positive_rate_both_seen": (
            float(yva[val_src_seen & val_dst_seen].mean()) if np.any(val_src_seen & val_dst_seen) else None
        ),
        "account_id_numeric_auc_for_mule_status": assignment_auc,
        "remapped_id_seed": int(cfg.project.seed) + 41,
        "remapped_unseen_id_pr_auc": remapped_ap,
        "remapped_unseen_id_pass": bool(remapped_ap <= 2.0 * base),
    }


def l3_label_shuffle(feat_dir: Path, cfg: Config) -> dict:
    from chainwatch.model.train import train_detector
    from chainwatch.model.vector_predict import VectorGBDT

    with tempfile.TemporaryDirectory() as tmp:
        cfg2 = cfg.with_overrides({"model.min_train_positives": 1, "model.n_estimators": 200})
        # The shuffled-label control must not use real validation labels to pick an iteration.
        # Train for the fixed configured budget, then evaluate once on validation.
        rep = train_detector(
            cfg2,
            feat_dir,
            "G2",
            tmp,
            label_shuffle=True,
            model_name="G2_shuffled",
            early_stopping=False,
        )
        va = load_split(feat_dir, "val", rep["feature_names"])
        s = VectorGBDT.load(Path(tmp) / "G2_shuffled.json").predict(np.asarray(va["X"]))
    yva = va["y"].astype(int)
    ap = M.average_precision(yva, s)
    base = float(yva.mean())
    return {"pass": bool(ap <= 2.0 * base), "pr_auc": ap, "positive_rate": base, "bound": 2.0 * base}


def l4_single_feature(feat_dir: Path, top: int = 10, flag_at: float = 0.98) -> dict:
    va = load_split(feat_dir, "val")
    y = va["y"].astype(int)
    aucs = {}
    for j, name in enumerate(va["names"]):
        col = np.asarray(va["X"][:, j], dtype=np.float64)
        a = M.roc_auc(y, col)
        aucs[name] = max(a, 1.0 - a) if np.isfinite(a) else float("nan")
    ranked = sorted(((n, a) for n, a in aucs.items() if np.isfinite(a)), key=lambda kv: -kv[1])
    flagged = [n for n, a in ranked if a > flag_at]
    return {
        "pass": True,
        "top": [{"feature": n, "auc": a} for n, a in ranked[:top]],
        "above_0.98": flagged,
        "note": "report only; features above 0.98 need a manual note in docs/decisions.md",
    }


def l5_importance(models_dir: Path | None) -> dict:
    if models_dir is None or not (Path(models_dir) / "train_report_G2.json").exists():
        return {"pass": True, "skipped": "no train report"}
    rep = json.loads((Path(models_dir) / "train_report_G2.json").read_text(encoding="utf-8"))
    share = rep["importance_gain_share"]
    groups: dict[str, float] = {}
    for n, s in share.items():
        groups[BY_NAME[n].group] = groups.get(BY_NAME[n].group, 0.0) + s
    bal = sum(share.get(n, 0.0) for n in BALANCE_FEATURES)
    return {"pass": True, "gain_share_by_group": groups, "balance_feature_share": bal}


def l6_account_reuse(feat_dir: Path, meta: dict, eval_path: Path | None) -> dict:
    wd = _world_dir(meta)
    rings = json.loads((wd / "rings.json").read_text(encoding="utf-8"))["rings"]
    b = meta["split_ts"]
    train_rings = [r for r in rings if b["burn_end"] <= r["first_ts"] < b["train_end"]]
    test_rings = [r for r in rings if b["val_end"] <= r["first_ts"] < b["test_end"]]
    seen = set()
    for r in train_rings:
        seen.update(r["accounts"]["mules"] + r["accounts"]["collectors"])
    test_mules = [a for r in test_rings for a in r["accounts"]["mules"] + r["accounts"]["collectors"]]
    reused = sum(1 for a in test_mules if a in seen)
    unseen_rings = {
        r["ring_id"]
        for r in test_rings
        if not (set(r["accounts"]["mules"] + r["accounts"]["collectors"]) & seen)
    }
    out = {
        "pass": True,
        "test_mule_accounts": len(test_mules),
        "seen_in_train_rings": reused,
        "share_reused": reused / len(test_mules) if test_mules else None,
        "test_rings": len(test_rings),
        "test_rings_all_unseen": len(unseen_rings),
    }
    if eval_path is not None and Path(eval_path).exists():
        ev = json.loads(Path(eval_path).read_text(encoding="utf-8"))
        sub = {}
        for v in ("B1+R", "G2+R"):
            units = ev["units"].get(v, {}).get("rings", [])
            all_c = [u["caught"] for u in units]
            un_c = [u["caught"] for u in units if u["ring_id"] in unseen_rings]
            sub[v] = {
                "recall_all": float(np.mean(all_c)) if all_c else None,
                "recall_unseen_only": float(np.mean(un_c)) if un_c else None,
                "n_unseen": len(un_c),
            }
        out["recall_subset"] = sub
        g = sub.get("G2+R", {})
        if g.get("recall_all") is not None and g.get("recall_unseen_only") is not None:
            out["pass"] = bool(g["recall_unseen_only"] >= g["recall_all"] - 0.15)
            out["criterion"] = "unseen-account subset recall within 15 points of the whole"
    return out


def l7_threshold_hygiene(models_dir: Path | None, meta: dict) -> dict:
    if models_dir is None or not (Path(models_dir) / "thresholds.json").exists():
        return {"pass": False, "reason": "no thresholds.json"}
    thr = json.loads((Path(models_dir) / "thresholds.json").read_text(encoding="utf-8"))
    cf = thr.get("created_from", {})
    ok_split = cf.get("split") == "validation"
    man = json.loads((Path(models_dir) / "manifest.json").read_text(encoding="utf-8"))
    ok_world = cf.get("world_events_sha256") == man.get("world_events_sha256")
    same_world = cf.get("world_events_sha256") == meta.get("world_events_sha256")
    return {
        "pass": bool(ok_split and ok_world),
        "split": cf.get("split"),
        "threshold_world_matches_training": ok_world,
        "evaluated_world_is_training_world": same_world,
    }


def l8_burn_in(feat_dir: Path, meta: dict) -> dict:
    burn = meta["split_ts"]["burn_end"]
    mins = [
        float(np.load(feat_dir / f"{s}_ts.npy").min())
        for s in ("train", "val", "test")
        if np.load(feat_dir / f"{s}_ts.npy").size
    ]
    return {
        "pass": bool(all(m >= burn for m in mins)),
        "burn_end": burn,
        "min_ts": min(mins) if mins else None,
    }


def l9_no_labels() -> dict:
    from pydantic import ValidationError

    from chainwatch.schema import LABEL_FIELDS, Event, EventIn

    base = {"event_id": 1, "ts": 0.0, "src": "A1", "dst": "A2", "amount": 1.0, "type": "TRANSFER"}
    rejected = []
    for f in LABEL_FIELDS:
        try:
            EventIn.model_validate({**base, f: 1})
        except ValidationError:
            rejected.append(f)
    no_attr = all(f not in Event.__slots__ for f in LABEL_FIELDS)
    return {
        "pass": bool(len(rejected) == len(LABEL_FIELDS) and no_attr),
        "rejected_fields": rejected,
        "event_type_has_no_label_fields": no_attr,
    }


def run_leakage(
    cfg: Config, feat_dir: str | Path, models_dir: str | Path | None = None, skip_slow: bool = False
) -> dict:
    feat_dir = Path(feat_dir)
    models_dir = Path(models_dir) if models_dir else None
    meta = json.loads((feat_dir / "meta.json").read_text(encoding="utf-8"))
    tag = meta.get("world")
    eval_path = ROOT / "results" / f"eval_{tag}.json"
    checks = {
        "L1": l1_temporal(feat_dir, meta, cfg),
        "L2": {"pass": True, "skipped": "fast mode"} if skip_slow else l2_id_only(feat_dir, cfg),
        "L3": {"pass": True, "skipped": "fast mode"} if skip_slow else l3_label_shuffle(feat_dir, cfg),
        "L4": l4_single_feature(feat_dir),
        "L5": l5_importance(models_dir),
        "L6": l6_account_reuse(feat_dir, meta, eval_path),
        "L7": l7_threshold_hygiene(models_dir, meta),
        "L8": l8_burn_in(feat_dir, meta),
        "L9": l9_no_labels(),
    }
    out = {
        "world": tag,
        "provenance": meta.get("provenance", "ChainWorld"),
        "checks": checks,
        "blocking_ok": all(checks[k]["pass"] for k in BLOCKING),
        "bug_signal": not (checks["L2"]["pass"] and checks["L3"]["pass"]),
    }
    path = ROOT / "results" / f"leakage_{tag}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=float)
    return out
