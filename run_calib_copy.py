import sys
import os
import tempfile
import json
import numpy as np
from pathlib import Path
import datetime as _dt

sys.stdout.flush()
sys.stderr.flush()

print("Starting copy of calibrate_world...")
sys.stdout.flush()

from chainwatch.config import ROOT
from chainwatch.data.world.generate import generate_world
from chainwatch.features.matrix import build_matrices
from chainwatch.model.train import train_detector
from chainwatch.model.vector_predict import VectorGBDT
from chainwatch.eval import metrics as M
from chainwatch.eval.protocol import _now, RESULTS

def calibrate_world_copy(cfg, preset: str, seed: int, tag: str = "calibration") -> dict:
    """Difficulty check with B0 and B1 ONLY (never G2): validation recall at 1% FPR, PR-AUC, fraud share."""
    print("  Inside calibrate_world_copy")
    sys.stdout.flush()
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
    print("  Created out dict")
    sys.stdout.flush()
    with tempfile.TemporaryDirectory(dir=ROOT / "data" / "processed") as tmp:
        print(f"  Using tmp dir: {tmp}")
        sys.stdout.flush()
        wd = Path(tmp) / f"world_{preset}_seed{seed}"
        print(f"  Generating world to {wd}")
        sys.stdout.flush()
        rep = generate_world(cfg, seed, wd)
        print("  World generated")
        sys.stdout.flush()
        fd = Path(tmp) / f"features_{wd.name}"
        print(f"  Building matrices to {fd}")
        sys.stdout.flush()
        build_matrices(cfg, wd, fd)
        print("  Matrices built")
        sys.stdout.flush()
        md = Path(tmp) / "models"
        print(f"  Training models to {md}")
        sys.stdout.flush()
        for d in ("B0", "B1"):
            print(f"    Training detector {d}")
            sys.stdout.flush()
            r = train_detector(cfg, fd, d, md, seed=seed)
            print(f"    Trained {d}")
            sys.stdout.flush()
            va = load_split(fd, "val", r["feature_names"])
            print(f"    Loaded val split")
            sys.stdout.flush()
            s = VectorGBDT.load(md / f"{d}.json").predict(np.asarray(va["X"]))
            print(f"    Predicted val")
            sys.stdout.flush()
            yv = va["y"].astype(int)
            print(f"    Computing metrics")
            sys.stdout.flush()
            out["detectors"][d] = {
                "pr_auc_val": M.average_precision(yv, s),
                "recall_at_1pct_fpr_val": M.recall_at_fpr_curve(yv, s, 0.01)["recall"],
                "recall_at_0.5pct_fpr_val": M.recall_at_fpr_curve(yv, s, 0.005)["recall"],
            }
        print("  Finished detector loop")
        sys.stdout.flush()
        out["fraud_share"] = rep["fraud_share"]
        out["fraud_share_in_band"] = rep["fraud_share_in_band"]
        out["n_events"] = rep["n_events"]
    print("  After temp dir")
    sys.stdout.flush()
    b1 = out["detectors"]["B1"]["recall_at_1pct_fpr_val"]
    out["b1_in_target_band"] = b1 is not None and 0.4 <= b1 <= 0.9
    print(f"  B1 recall: {b1}, in target band: {out['b1_in_target_band']}")
    sys.stdout.flush()
    path = RESULTS / f"calibration_{tag}.json"
    print(f"  Saving results to {path}")
    sys.stdout.flush()
    RESULTS.mkdir(parents=True, exist_ok=True)
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": []}
    out["utc"] = _now()
    hist["runs"].append(out)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, indent=1)
    print("  Done")
    sys.stdout.flush()
    return out

def load_split(feat_dir: Path, split: str, feature_names: list[str]) -> dict:
    from chainwatch.features.matrix import load_split
    return load_split(feat_dir, split, feature_names)

# Now load config and apply overrides
from chainwatch.config import load

print("Loading config...")
sys.stdout.flush()
cfg = load(preset='medium')
print("Loaded config")
sys.stdout.flush()

overrides = {
    'world.noise_level': 2.0,
    'world.hard_negative_boost': 20.0,
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
cfg = cfg.with_overrides(overrides)
print("Applied overrides")
sys.stdout.flush()

print("Calling calibrate_world_copy...")
sys.stdout.flush()
result = calibrate_world_copy(cfg, preset='medium', seed=1000, tag='medium_copy')
print("Returned from calibrate_world_copy")
sys.stdout.flush()

b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']
b1_in_target = result['b1_in_target_band']

print(f"B1 recall_at_1pct_fpr_val: {b1_recall}")
print(f"Fraud share: {fraud_share}")
print(f"b1_in_target_band: {b1_in_target}")
sys.stdout.flush()