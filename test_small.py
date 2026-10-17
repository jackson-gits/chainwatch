#!/usr/bin/env python3
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

def main():
    cfg = load(preset="small")
    print("Starting calibration on small preset...")
    start = time.time()
    out = calibrate_world(cfg, preset="small", seed=0, tag="test_small")
    elapsed = time.time() - start
    print(f"Elapsed time: {elapsed:.1f} seconds")
    print(f"B1 recall@1%FPR: {out['detectors']['B1']['recall_at_1pct_fpr_val']:.3f}")
    print(f"Fraud share: {out['fraud_share']:.4f}")

if __name__ == "__main__":
    main()