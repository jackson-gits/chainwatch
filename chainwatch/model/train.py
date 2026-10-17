"""LightGBM training for B0, B1 and G2 (train env, spec 10.2).

All detectors share hyperparameters, early stopping and the data split (fairness rule H2). After export the
portable JSON is checked against the booster (parity gate); training fails if they disagree.
"""

from __future__ import annotations

import json
import logging
import platform
import time
from pathlib import Path

import numpy as np

from chainwatch.config import Config
from chainwatch.features.matrix import load_split
from chainwatch.features.registry import names_for_cfg, registry_hash, semantic_features_config
from chainwatch.model.export import booster_to_portable, write_model
from chainwatch.model.portable_gbdt import PortableGBDT

log = logging.getLogger(__name__)
PARITY_TOL = 1e-7


class TrainError(RuntimeError):
    pass


def lgb_params(cfg: Config, seed: int, overrides: dict | None = None) -> dict:
    m = cfg.model
    p = dict(
        objective="binary",
        metric="average_precision",
        learning_rate=float(m.learning_rate),
        num_leaves=int(m.num_leaves),
        max_depth=int(m.max_depth),
        min_child_samples=int(m.min_child_samples),
        subsample=float(m.subsample),
        subsample_freq=int(m.subsample_freq),
        colsample_bytree=float(m.colsample_bytree),
        reg_lambda=float(m.reg_lambda),
        max_bin=255,
        seed=int(seed),
        deterministic=bool(m.deterministic),
        force_row_wise=True,
        use_missing=bool(m.use_missing),
        zero_as_missing=bool(m.zero_as_missing),
        num_threads=int(m.n_jobs),
        verbosity=-1,
    )
    if overrides:
        p.update(overrides)
    return p


def detector_names(cfg: Config, detector: str, extra_disabled: list[str] | tuple[str, ...] = ()) -> list[str]:
    names = names_for_cfg(cfg, detector)
    return [n for n in names if n not in set(extra_disabled)]


def train_detector(
    cfg: Config,
    feat_dir: str | Path,
    detector: str,
    out_dir: str | Path,
    seed: int | None = None,
    extra_disabled: list[str] | tuple[str, ...] = (),
    model_name: str | None = None,
    param_overrides: dict | None = None,
    label_shuffle: bool = False,
    early_stopping: bool = True,
) -> dict:
    """Train one detector, export JSON, run the parity gate. Returns the train report."""
    import lightgbm as lgb
    from sklearn.metrics import average_precision_score

    t0 = time.time()
    feat_dir, out_dir = Path(feat_dir), Path(out_dir)
    seed = int(cfg.project.seed if seed is None else seed)
    names = detector_names(cfg, detector, extra_disabled)
    tr = load_split(feat_dir, "train", names)
    va = load_split(feat_dir, "val", names)
    meta = json.loads((feat_dir / "meta.json").read_text(encoding="utf-8"))
    y = tr["y"].astype(np.int64)
    if label_shuffle:
        y = np.random.default_rng(seed + 7).permutation(y)
    pos = np.flatnonzero(y == 1)
    min_pos = int(cfg.model.get("min_train_positives", 200))
    if pos.size < min_pos:
        raise TrainError(
            f"only {pos.size} positives in the training split (need {min_pos}); use a larger preset"
        )
    rng = np.random.default_rng(seed)
    neg = np.flatnonzero(y == 0)
    ds = float(cfg.model.neg_downsample)
    keep_neg = neg[rng.random(neg.size) < ds]
    idx = np.sort(np.concatenate([pos, keep_neg]))
    X_tr = np.asarray(tr["X"][idx], dtype=np.float64)
    y_tr = y[idx]
    w_tr = np.where(y_tr == 1, 1.0, 1.0 / ds)
    X_va = np.asarray(va["X"], dtype=np.float64)
    y_va = va["y"].astype(np.int64)
    params = lgb_params(cfg, seed, param_overrides)
    dtrain = lgb.Dataset(
        X_tr,
        y_tr,
        weight=w_tr,
        feature_name=names,
        free_raw_data=False,
        params={"max_bin": 255, "verbosity": -1},
    )
    dval = lgb.Dataset(X_va, y_va, reference=dtrain, free_raw_data=False)
    n_est = int(cfg.model.n_estimators)
    callbacks = [lgb.log_evaluation(0)]
    if early_stopping:
        callbacks.insert(0, lgb.early_stopping(int(cfg.model.early_stopping_rounds), verbose=False))
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=n_est,
        valid_sets=[dval],
        callbacks=callbacks,
    )
    best = int(booster.best_iteration or booster.current_iteration())
    ship = min(best, int(cfg.model.ship_max_trees))
    rh = registry_hash(names, semantic_features_config(cfg))
    trained_on = {
        "world": meta.get("world"),
        "provenance": meta.get("provenance", "ChainWorld"),
        "config_hash": cfg.config_hash,
        "train_rows": int(idx.size),
        "pos_rows": int(pos.size),
        "lightgbm": lgb.__version__,
        "label_shuffle": bool(label_shuffle),
    }
    model = booster_to_portable(booster, detector, names, rh, ship, trained_on)
    pg = PortableGBDT(model)

    # parity gate on random validation rows
    k = min(5000, X_va.shape[0])
    rows = np.sort(np.random.default_rng(seed + 1).choice(X_va.shape[0], size=k, replace=False))
    ref = booster.predict(X_va[rows], num_iteration=ship)
    mine = np.array([pg.predict(X_va[r].tolist()) for r in rows])
    max_diff = float(np.max(np.abs(ref - mine))) if k else 0.0
    if max_diff >= PARITY_TOL:
        raise TrainError(
            f"{detector}: portable model differs from LightGBM by {max_diff:.3g} (>= {PARITY_TOL})"
        )

    name = model_name or detector
    out_dir.mkdir(parents=True, exist_ok=True)
    write_model(model, out_dir / f"{name}.json")
    p_va_best = booster.predict(X_va, num_iteration=best)
    p_va_ship = booster.predict(X_va, num_iteration=ship) if ship != best else p_va_best
    p_tr = booster.predict(X_tr, num_iteration=ship)
    gain = booster.feature_importance(importance_type="gain", iteration=ship)
    total_gain = float(gain.sum()) or 1.0
    report = {
        "detector": detector,
        "model_name": name,
        "provenance": trained_on["provenance"],
        "world": meta.get("world"),
        "feature_names": names,
        "n_features": len(names),
        "registry_hash": rh,
        "params": params,
        "n_estimators": n_est,
        "best_iteration": best,
        "shipped_trees": ship,
        "truncated": ship < best,
        "pr_auc_train_weighted": float(average_precision_score(y_tr, p_tr, sample_weight=w_tr)),
        "pr_auc_val_best": float(average_precision_score(y_va, p_va_best)) if y_va.any() else None,
        "pr_auc_val_shipped": float(average_precision_score(y_va, p_va_ship)) if y_va.any() else None,
        "parity_max_abs_diff": max_diff,
        "parity_rows": int(k),
        "importance_gain": {n: float(g) for n, g in zip(names, gain, strict=False)},
        "importance_gain_share": {n: float(g) / total_gain for n, g in zip(names, gain, strict=False)},
        "rows": {
            "train_total": int(y.size),
            "train_used": int(idx.size),
            "train_pos": int(pos.size),
            "val": int(y_va.size),
            "val_pos": int(y_va.sum()),
        },
        "neg_downsample": ds,
        "seed": seed,
        "label_shuffle": bool(label_shuffle),
        "extra_disabled": list(extra_disabled),
        "wall_seconds": round(time.time() - t0, 1),
        "versions": {
            "lightgbm": lgb.__version__,
            "numpy": np.__version__,
            "python": platform.python_version(),
        },
    }
    with open(out_dir / f"train_report_{name}.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    log.info(
        "trained %s: best_iter=%d shipped=%d val PR-AUC=%.4f parity=%.2g (%.0fs)",
        name,
        best,
        ship,
        report["pr_auc_val_shipped"] or float("nan"),
        max_diff,
        report["wall_seconds"],
    )
    return report


def train_all(
    cfg: Config,
    feat_dir: str | Path,
    out_dir: str | Path,
    detectors=("B0", "B1", "G2"),
    seed: int | None = None,
) -> dict:
    return {d: train_detector(cfg, feat_dir, d, out_dir, seed=seed) for d in detectors}


def tune_grid(
    cfg: Config,
    feat_dir: str | Path,
    out_dir: str | Path,
    detectors=("B0", "B1", "G2"),
    grid=(15, 31, 63),
    seed: int | None = None,
) -> dict:
    """Run the same num_leaves grid for every detector, choose on validation PR-AUC, log the winners."""
    import tempfile

    results: dict = {}
    for d in detectors:
        scores = {}
        for nl in grid:
            with tempfile.TemporaryDirectory() as tmp:
                rep = train_detector(cfg, feat_dir, d, tmp, seed=seed, param_overrides={"num_leaves": nl})
            scores[nl] = rep["pr_auc_val_shipped"]
        best = max(scores, key=lambda k: (scores[k] or 0.0, -k))
        results[d] = {"scores": scores, "num_leaves": best}
        train_detector(cfg, feat_dir, d, out_dir, seed=seed, param_overrides={"num_leaves": best})
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(out_dir) / "tuning_grid.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    return results
