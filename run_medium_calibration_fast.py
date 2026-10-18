import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting medium calibration with reduced size for speed...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

print("Loaded imports")
sys.stdout.flush()

# Load base config for preset medium
cfg = load(preset='medium')
print("Loaded medium config")
sys.stdout.flush()

# Apply requested overrides
overrides = {
    'world.noise_level': 2.0,
    'world.hard_negative_boost': 20.0,
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
# Additionally reduce sizes to speed up generation
overrides.update({
    'world.n_customers': 1000,
    'world.n_merchants': 50,
    'world.n_traders': 10,
    'world.n_salary_payers': 5,
    'world.n_landlords': 50,
    'world.n_agents': 10,
})
cfg = cfg.with_overrides(overrides)
print(f"Applied overrides: n_customers={cfg.world.n_customers}, n_merchants={cfg.world.n_merchants}, n_campaigns={cfg.world.n_campaigns}")
sys.stdout.flush()

# Run calibration
print("Calling calibrate_world...")
sys.stdout.flush()
result = calibrate_world(cfg, preset='medium', seed=1000, tag='medium_fast')
print("Finished calibrate_world")
sys.stdout.flush()

# Extract and print
b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']
b1_in_target = result['b1_in_target_band']

print(f"B1 recall_at_1pct_fpr_val: {b1_recall}")
print(f"Fraud share: {fraud_share}")
print(f"b1_in_target_band: {b1_in_target}")
sys.stdout.flush()