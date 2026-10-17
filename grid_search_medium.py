#!/usr/bin/env python3
"""Grid search for medium preset parameters to meet B1 recall at 1% FPR between 0.4 and 0.9
and fraud share between 0.0008 and 0.004.

Varied parameters: noise_level, hard_negative_boost, mule_mix (new, dormant, active),
optionally n_campaigns.
"""

import sys
from pathlib import Path

# Ensure we can import chainwatch from repo root
sys.path.insert(0, str(Path(__file__).parent))

from chainwatch.config import Config, load
from chainwatch.eval.protocol import calibrate_world

def main():
    # Base config with preset medium
    base_cfg = load(preset="medium")
    print("Base config loaded:")
    print(f"  noise_level: {base_cfg.world.noise_level}")
    print(f"  hard_negative_boost: {base_cfg.world.hard_negative_boost}")
    print(f"  mule_mix: {base_cfg.world.mule_mix}")
    print(f"  n_campaigns: {base_cfg.world.n_campaigns}")
    print(f"  fraud_target_share: {base_cfg.world.fraud_target_share}")

    # Define grids
    noise_levels = [0.5, 1.5, 2.0]
    hard_boosts = [1.0, 2.0, 3.0]
    # mule_mix grid: new and dormant fractions, active = 1 - new - dormant
    mule_new_vals = [0.3, 0.4, 0.5]
    mule_dormant_vals = [0.2, 0.3, 0.4]
    n_campaigns_vals = [100, 140, 180]

    results = []
    seed = 0  # fixed seed for speed
    tag_prefix = "grid_search"

    for nl in noise_levels:
        for hb in hard_boosts:
            for new in mule_new_vals:
                for dor in mule_dormant_vals:
                    act = 1.0 - new - dor
                    if act < 0:
                        continue  # skip invalid mix
                    for nc in n_campaigns_vals:
                        # Build overrides dict
                        overrides = {
                            "world.noise_level": nl,
                            "world.hard_negative_boost": hb,
                            "world.mule_mix.new": new,
                            "world.mule_mix.dormant": dor,
                            "world.mule_mix.active": act,
                            "world.n_campaigns": nc,
                        }
                        cfg = base_cfg.with_overrides(overrides)
                        # Run calibration
                        try:
                            out = calibrate_world(cfg, preset="medium", seed=seed, tag=f"{tag_prefix}_{nl}_{hb}_{new}_{dor}_{act}_{nc}")
                        except Exception as e:
                            print(f"Error with params {overrides}: {e}")
                            continue

                        b1_recall = out["detectors"]["B1"]["recall_at_1pct_fpr_val"]
                        fraud_share = out["fraud_share"]
                        in_band = out["b1_in_target_band"]
                        fraud_in_target = 0.0008 <= fraud_share <= 0.004

                        print(f"Tested: noise={nl}, boost={hb}, mule=new:{new:.2f}, dormant:{dor:.2f}, active:{act:.2f}, campaigns={nc}")
                        print(f"  B1 recall@1%FPR: {b1_recall:.3f} (in 0.4-0.9: {in_band})")
                        print(f"  Fraud share: {fraud_share:.4f} (in 0.0008-0.004: {fraud_in_target})")

                        if in_band and fraud_in_target:
                            results.append({
                                "params": overrides.copy(),
                                "b1_recall": b1_recall,
                                "fraud_share": fraud_share,
                                "output": out,
                            })
                            print("  -> SUCCESS")
                        else:
                            print("  -> FAIL")
                        print()

    if not results:
        print("No parameter sets satisfied constraints.")
        return

    # Choose best: minimize distance to center of ranges
    def score(r):
        b1 = r["b1_recall"]
        f = r["fraud_share"]
        # center of B1 range: 0.65, width 0.5 -> normalize deviation
        b1_dev = abs(b1 - 0.65) / 0.25  # max deviation 0.25 from center to edge
        # center of fraud range: 0.0024, width 0.0032 -> normalize
        f_dev = abs(f - 0.0024) / 0.0016
        return b1_dev + f_dev

    best = min(results, key=score)
    print("Best parameter set:")
    for k, v in best["params"].items():
        print(f"  {k}: {v}")
    print(f"B1 recall@1%FPR: {best['b1_recall']:.3f}")
    print(f"Fraud share: {best['fraud_share']:.4f}")

    # Also output full config for reproducibility
    cfg = base_cfg.with_overrides(best["params"])
    config_path = Path("best_medium_config.toml")
    # We'll dump the config as TOML? Simpler: output as JSON
    import json
    with open("best_medium_config.json", "w") as f:
        json.dump(cfg.to_dict(), f, indent=2)
    print(f"Full config saved to best_medium_config.json")

if __name__ == "__main__":
    main()