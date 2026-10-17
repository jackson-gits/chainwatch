"""Robustness grid R1 to R6 (spec 14.6). Models trained on the default medium world are NOT retrained
(except R6); new small-preset worlds (2 seeds each) are generated with one change at a time and scored
with ``B1+R`` and ``G2+R`` at the validation-fixed thresholds of the model package. Output:
``results/robustness.json``.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import numpy as np

from chainwatch.config import CONFIG_DIR, ROOT, Config, read_toml
from chainwatch.eval.protocol import RESULTS, _json_default, evaluate_core

log = logging.getLogger(__name__)
TYPS = ("funnel", "layered_chain", "split_merge", "round_trip", "structuring", "slow_roll")


def cell_overrides(cfg: Config, key: str, value) -> dict:
    if key == "world.mix.slow_roll":
        mix = {k: float(v) for k, v in cfg.world.mix.items()}
        rest = 1.0 - mix["slow_roll"]
        scale = (1.0 - float(value)) / rest
        new = {k: (float(value) if k == "slow_roll" else v * scale) for k, v in mix.items()}
        return {"world.mix": new}
    if key == "world.mule_mix":
        if value == "all_active":
            return {"world.mule_mix": {"new": 0.0, "dormant": 0.0, "active": 1.0}}
        return {}
    return {key: value}


def _run_world(cfg_dict: dict, preset: str, seed: int, overrides: dict, models_dir: str, name: str) -> dict:
    from chainwatch.config import Config, _to_section
    from chainwatch.data.world.generate import generate_world
    from chainwatch.features.matrix import build_matrices

    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{name}] %(name)s: %(message)s")
    cfg = Config(_to_section(cfg_dict))
    c2 = cfg.with_overrides({**{"world.preset": preset}, **_preset_counts(preset), **overrides})
    base = ROOT / "data" / "processed" / "robustness"
    wd = base / f"world_{name}_seed{seed}"
    fd = base / f"features_world_{name}_seed{seed}"
    if not (fd / "meta.json").exists():
        generate_world(c2, seed, wd)
        build_matrices(c2, wd, fd)
    res = evaluate_core(c2, fd, Path(models_dir), variants=("B1+R", "G2+R"), keep_units=True)
    rows = {}
    for v in ("B1+R", "G2+R"):
        rl, tl = res["ring_level"][v], res["transaction_level"][v]
        typ = {t: rl["by_typology"][t]["recall"]["p"] for t in rl["by_typology"]}
        rows[v] = {
            "ring_recall": rl["recall"]["p"],
            "n_rings": rl["n_rings"],
            "deployed_recall": tl["deployed"]["recall"]["p"],
            "test_fpr": tl["deployed"]["fpr"],
            "ttc_median_min": rl["ttc_min"]["median"],
            "recall_by_typology": typ,
        }
    return {"world": wd.name, "seed": seed, "results": rows}


def _preset_counts(preset: str) -> dict:
    data = read_toml(CONFIG_DIR / f"world_{preset}.toml")["world"]
    return {f"world.{k}": v for k, v in data.items() if k != "preset"}


def heldout_package(cfg: Config, typ: str = "round_trip", seed: int = 0) -> Path:
    """R6: train B0/B1/G2 on a medium world generated without ``typ``; thresholds on its validation split."""
    from chainwatch.data.world.generate import generate_world
    from chainwatch.features.matrix import build_matrices
    from chainwatch.model.thresholds import compute_thresholds
    from chainwatch.model.train import train_detector

    mix = {k: float(v) for k, v in cfg.world.mix.items()}
    rest = 1.0 - mix[typ]
    new = {k: (0.0 if k == typ else v / rest) for k, v in mix.items()}
    c2 = cfg.with_overrides({"world.mix": new})
    base = ROOT / "data" / "processed" / "robustness"
    wd = base / f"world_medium_no_{typ}_seed{seed}"
    fd = base / f"features_world_medium_no_{typ}_seed{seed}"
    md = ROOT / "models" / "eval" / f"heldout_{typ}"
    if not (md / "manifest.json").exists():
        if not (fd / "meta.json").exists():
            generate_world(c2, seed, wd)
            build_matrices(c2, wd, fd)
        for d in ("B0", "B1", "G2"):
            train_detector(c2, fd, d, md, seed=seed)
        compute_thresholds(c2, fd, md)
    return md


def run_robustness(
    cfg: Config,
    models_dir: str | Path = "models/default",
    seeds: list[int] | None = None,
    jobs: int = 1,
    cells: str | None = None,
) -> dict:
    spec = read_toml(CONFIG_DIR / "robustness.toml")
    seeds = seeds or list(spec.get("seeds", [100, 101]))
    preset = spec.get("preset", "small")
    wanted = set(cells.split(",")) if cells else None
    models_dir = str((ROOT / models_dir) if not Path(models_dir).is_absolute() else models_dir)
    tasks = []
    for cell in spec["cell"]:
        if wanted and cell["id"] not in wanted:
            continue
        if cell["key"] == "heldout_typology":
            continue
        for value in cell["values"]:
            name = f"{cell['id']}_{str(value).replace('.', 'p')}"
            ov = cell_overrides(cfg, cell["key"], value)
            for s in seeds:
                tasks.append((cell["id"], cell["key"], value, name, ov, s, models_dir))
    rows = []

    def collect(task, out):
        cid, key, value, name, _, s, _ = task
        for v, r in out["results"].items():
            rows.append({"cell": cid, "key": key, "value": value, "seed": s, "variant": v, **r})

    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = [(t, ex.submit(_run_world, cfg.to_dict(), preset, t[5], t[4], t[6], t[3])) for t in tasks]
            for t, f in futs:
                collect(t, f.result())
    else:
        for t in tasks:
            collect(t, _run_world(cfg.to_dict(), preset, t[5], t[4], t[6], t[3]))
    heldout = None
    if wanted is None or "R6" in wanted:
        for cell in spec["cell"]:
            if cell["key"] != "heldout_typology":
                continue
            for typ in cell["values"]:
                md = heldout_package(cfg, typ)
                for s in seeds:
                    out = _run_world(cfg.to_dict(), preset, s, {}, str(md), f"R6_default_for_{typ}")
                    ref = _run_world(cfg.to_dict(), preset, s, {}, models_dir, "R1_1p0")
                    for v in ("B1+R", "G2+R"):
                        r_ho = out["results"][v]["recall_by_typology"].get(typ)
                        r_ref = ref["results"][v]["recall_by_typology"].get(typ)
                        rows.append(
                            {
                                "cell": "R6",
                                "key": "heldout_typology",
                                "value": typ,
                                "seed": s,
                                "variant": v,
                                "ring_recall": out["results"][v]["ring_recall"],
                                "deployed_recall": out["results"][v]["deployed_recall"],
                                "test_fpr": out["results"][v]["test_fpr"],
                                "ttc_median_min": out["results"][v]["ttc_median_min"],
                                "heldout_typology_recall": r_ho,
                                "reference_typology_recall": r_ref,
                                "n_rings": out["results"][v]["n_rings"],
                            }
                        )
                heldout = str(md)
    table = []
    keyset = sorted({(r["cell"], str(r["value"]), r["variant"]) for r in rows})
    for cid, val, v in keyset:
        sel = [r for r in rows if r["cell"] == cid and str(r["value"]) == val and r["variant"] == v]

        def mean(k, sel=sel):
            xs = [r[k] for r in sel if r.get(k) is not None]
            return float(np.mean(xs)) if xs else None

        table.append(
            {
                "cell": cid,
                "value": val,
                "variant": v,
                "worlds": len(sel),
                "ring_recall": mean("ring_recall"),
                "deployed_recall": mean("deployed_recall"),
                "test_fpr": mean("test_fpr"),
                "ttc_median_min": mean("ttc_median_min"),
                "heldout_typology_recall": mean("heldout_typology_recall"),
                "reference_typology_recall": mean("reference_typology_recall"),
            }
        )
    out = {
        "provenance": "ChainWorld",
        "preset": preset,
        "seeds": seeds,
        "models": models_dir,
        "heldout_models": heldout,
        "rows": rows,
        "table": table,
        "note": "Models were not retrained (except R6). Thresholds are the package's validation thresholds.",
    }
    with open(RESULTS / "robustness.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=_json_default)
    return out


def cleanup_robustness_worlds() -> None:
    shutil.rmtree(ROOT / "data" / "processed" / "robustness", ignore_errors=True)
