#!/usr/bin/env python3
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

def main():
    base = load(preset="medium")
    # base overrides
    overrides = {
        "world.noise_level": 2.0,
        "world.hard_negative_boost": 20.0,
        "world.mule_mix.new": 0.0,
        "world.mule_mix.dormant": 0.0,
        "world.mule_mix.active": 1.0,
    }
    cfg = base.with_overrides(overrides)
    # vary n_campaigns
    n_campaigns_list = [140, 180, 200, 220, 260, 300]
    for nc in n_campaigns_list:
        cfg2 = cfg.with_overrides({"world.n_campaigns": nc})
        print(f"\nTesting n_campaigns={nc}...")
        start = time.time()
        try:
            out = calibrate_world(cfg2, preset="medium", seed=1000, tag=f"test_nc{nc}")
            elapsed = time.time() - start
            b1_recall = out["detectors"]["B1"]["recall_at_1pct_fpr_val"]
            fraud_share = out["fraud_share"]
            print(f"  Time: {elapsed:.1f}s")
            print(f"  B1 recall@1%FPR: {b1_recall:.3f}")
            print(f"  Fraud share: {fraud_share:.4f}")
            if 0.4 <= b1_recall <= 0.9 and 0.0008 <= fraud_share <= 0.004:
                print("  -> SATISFIES CONSTRAINTS")
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == "__main__":
    main()