#!/usr/bin/env python3
"""Final search over n_campaigns and mule_mix around the promising region:
noise_level=2.0, hard_negative_boost=20.0, mule_mix mostly active.
"""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

def main():
    base = load(preset="medium")
    # Fixed overrides
    fixed = {
        "world.noise_level": 2.0,
        "world.hard_negative_boost": 20.0,
    }
    cfg_base = base.with_overrides(fixed)
    
    # Grid for n_campaigns and mule_mix active fraction (new and dormant zero)
    n_campaigns_vals = [140, 180, 200, 220, 260, 300]
    active_vals = [0.8, 0.9, 1.0]  # remaining share split between new and dormant? we'll set new=0, dormant=0, active=val
    # Actually mule_mix must sum to 1; we'll set new=0, dormant=0, active=val, but then sum=val, need to adjust.
    # Instead we'll vary active and set new and dormant to share remainder equally? Let's keep new=0, dormant=0, active=1.0 fixed and only vary n_campaigns.
    # For simplicity, we'll just vary n_campaigns with mule_mix all active.
    results = []
    for nc in n_campaigns_vals:
        overrides = {
            "world.n_campaigns": nc,
            "world.mule_mix.new": 0.0,
            "world.mule_mix.dormant": 0.0,
            "world.mule_mix.active": 1.0,
        }
        cfg = cfg_base.with_overrides(overrides)
        print(f"\nTesting n_campaigns={nc}, mule_mix all active...")
        start = time.time()
        try:
            out = calibrate_world(cfg, preset="medium", seed=1000, tag=f"final_nc{nc}")
            elapsed = time.time() - start
            b1_recall = out["detectors"]["B1"]["recall_at_1pct_fpr_val"]
            fraud_share = out["fraud_share"]
            in_band = 0.4 <= b1_recall <= 0.9
            fraud_in_target = 0.0008 <= fraud_share <= 0.004
            print(f"  Time: {elapsed:.1f}s")
            print(f"  B1 recall@1%FPR: {b1_recall:.3f} (in target: {in_band})")
            print(f"  Fraud share: {fraud_share:.4f} (in target: {fraud_in_target})")
            if in_band and fraud_in_target:
                results.append({
                    "n_campaigns": nc,
                    "b1_recall": b1_recall,
                    "fraud_share": fraud_share,
                })
                print("  -> SUCCESS")
        except Exception as e:
            print(f"  Error: {e}")
            continue

    if not results:
        print("\nNo parameter sets satisfied constraints.")
        return

    # Choose the one with fraud share closest to 0.002 (midpoint) and B1 recall closest to 0.65
    def score(r):
        b1_dev = abs(r["b1_recall"] - 0.65) / 0.25
        f_dev = abs(r["fraud_share"] - 0.0024) / 0.0016
        return b1_dev + f_dev

    best = min(results, key=score)
    print("\nBest parameter set:")
    print(f"  noise_level: 2.0")
    print(f"  hard_negative_boost: 20.0")
    print(f"  mule_mix: new=0.0, dormant=0.0, active=1.0")
    print(f"  n_campaigns: {best['n_campaigns']}")
    print(f"  B1 recall@1%FPR: {best['b1_recall']:.3f}")
    print(f"  Fraud share: {best['fraud_share']:.4f}")

    # Save config
    cfg = load(preset="medium")
    overrides = {
        "world.noise_level": 2.0,
        "world.hard_negative_boost": 20.0,
        "world.mule_mix.new": 0.0,
        "world.mule_mix.dormant": 0.0,
        "world.mule_mix.active": 1.0,
        "world.n_campaigns": best["n_campaigns"],
    }
    final_cfg = cfg.with_overrides(overrides)
    import json
    with open("final_medium_config.json", "w") as f:
        json.dump(final_cfg.to_dict(), f, indent=2)
    print(f"\nFull config saved to final_medium_config.json")

if __name__ == "__main__":
    main()