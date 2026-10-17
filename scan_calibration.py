#!/usr/bin/env python3
import json
from pathlib import Path

results_dir = Path("results")
# Find all json files that contain 'medium' and 'calibration' in name
files = [f for f in results_dir.glob("*.json") if "calibration" in f.name.lower() and "medium" in f.name.lower()]
print(f"Found {len(files)} calibration files for medium preset.")

candidates = []
for f in files:
    try:
        with open(f, "r") as fp:
            data = json.load(fp)
        # each file has a "runs" list with one run
        run = data["runs"][0]
        b1_recall = run["detectors"]["B1"]["recall_at_1pct_fpr_val"]
        fraud_share = run["fraud_share"]
        in_band = run["b1_in_target_band"]
        fraud_in_target = 0.0008 <= fraud_share <= 0.004
        world_params = run["world_params"]
        noise = world_params["noise_level"]
        boost = world_params["hard_negative_boost"]
        mule_mix = world_params["mule_mix"]
        if in_band and fraud_in_target:
            candidates.append({
                "file": f.name,
                "noise": noise,
                "boost": boost,
                "mule_mix": mule_mix,
                "b1_recall": b1_recall,
                "fraud_share": fraud_share,
            })
    except Exception as e:
        print(f"Error reading {f}: {e}")

print(f"\nFound {len(candidates)} candidates satisfying both constraints.")
if candidates:
    print("\nCandidates:")
    for c in candidates:
        print(f"File: {c['file']}")
        print(f"  noise_level: {c['noise']}, hard_negative_boost: {c['boost']}")
        print(f"  mule_mix: {c['mule_mix']}")
        print(f"  B1 recall@1%FPR: {c['b1_recall']:.3f}")
        print(f"  Fraud share: {c['fraud_share']:.4f}")
        print()
else:
    print("No candidates found in existing calibration files.")
    # Let's output all files with their values to see ranges
    print("\nAll files:")
    for f in files:
        try:
            with open(f, "r") as fp:
                data = json.load(fp)
            run = data["runs"][0]
            b1_recall = run["detectors"]["B1"]["recall_at_1pct_fpr_val"]
            fraud_share = run["fraud_share"]
            world_params = run["world_params"]
            noise = world_params["noise_level"]
            boost = world_params["hard_negative_boost"]
            print(f"{f.name}: noise={noise}, boost={boost}, B1_recall={b1_recall:.3f}, fraud={fraud_share:.4f}")
        except Exception as e:
            pass