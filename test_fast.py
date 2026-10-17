#!/usr/bin/env python3
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

def main():
    cfg = load(preset="medium")
    # Speed overrides
    overrides = {
        "world.noise_level": 1.0,
        "world.hard_negative_boost": 2.0,
        "world.mule_mix.new": 0.4,
        "world.mule_mix.dormant": 0.3,
        "world.mule_mix.active": 0.3,
        "world.n_campaigns": 140,
        "model.n_estimators": 20,
        # maybe also reduce num_leaves? keep default
    }
    cfg = cfg.with_overrides(overrides)
    print("Starting calibration with fast model...")
    start = time.time()
    out = calibrate_world(cfg, preset="medium", seed=0, tag="test_fast")
    elapsed = time.time() - start
    print(f"Elapsed time: {elapsed:.1f} seconds")
    print(f"B1 recall@1%FPR: {out['detectors']['B1']['recall_at_1pct_fpr_val']:.3f}")
    print(f"Fraud share: {out['fraud_share']:.4f}")

if __name__ == "__main__":
    main()