import sys
import os
import logging
import time

sys.stdout.flush()
sys.stderr.flush()

print("Starting calibration with logging...")
sys.stdout.flush()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger(__name__)

from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

print("Loaded imports")
sys.stdout.flush()

cfg = load(preset='medium')
print("Loaded medium config")
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
print(f"Applied overrides")
sys.stdout.flush()

print("Calling calibrate_world at", time.time())
sys.stdout.flush()
start = time.time()
result = calibrate_world(cfg, preset='medium', seed=1000, tag='medium_log')
print(f"Finished calibrate_world after {time.time()-start} seconds")
sys.stdout.flush()

b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']
b1_in_target = result['b1_in_target_band']

print(f"B1 recall_at_1pct_fpr_val: {b1_recall}")
print(f"Fraud share: {fraud_share}")
print(f"b1_in_target_band: {b1_in_target}")
sys.stdout.flush()