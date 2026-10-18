import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting small calibration (base config)...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

print("Loaded imports")
sys.stdout.flush()

cfg = load(preset='small')
print("Loaded small config")
sys.stdout.flush()

# No overrides
print("Calling calibrate_world...")
sys.stdout.flush()
result = calibrate_world(cfg, preset='small', seed=1000, tag='small_base')
print("Finished calibrate_world")
sys.stdout.flush()

b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']
b1_in_target = result['b1_in_target_band']

print(f"B1 recall_at_1pct_fpr_val: {b1_recall}")
print(f"Fraud share: {fraud_share}")
print(f"b1_in_target_band: {b1_in_target}")
sys.stdout.flush()