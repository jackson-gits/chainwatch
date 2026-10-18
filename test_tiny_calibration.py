import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting tiny calibration...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

print("Loaded imports")
sys.stdout.flush()

# Load tiny config
cfg = load(preset='tiny')
print("Loaded tiny config")
sys.stdout.flush()

overrides = {
    'world.noise_level': 2.0,
    'world.hard_negative_boost': 20.0,
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,  # tiny default is 12, but we override
}
cfg = cfg.with_overrides(overrides)
print("Applied overrides")
sys.stdout.flush()

print("Calling calibrate_world...")
sys.stdout.flush()
result = calibrate_world(cfg, preset='tiny', seed=1000, tag='tiny_overrides')
print("Finished calibrate_world")
sys.stdout.flush()

b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']
b1_in_target = result['b1_in_target_band']

print(f"B1 recall_at_1pct_fpr_val: {b1_recall}")
print(f"Fraud share: {fraud_share}")
print(f"b1_in_target_band: {b1_in_target}")
sys.stdout.flush()