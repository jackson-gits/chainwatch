from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

print("Loading config...")
# Load base config for preset medium
cfg = load(preset='medium')
print("Config loaded.")

# Apply overrides
overrides = {
    'world.noise_level': 2.0,
    'world.hard_negative_boost': 20.0,
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
print("Applying overrides...")
cfg = cfg.with_overrides(overrides)
print("Overrides applied.")

# Run calibration
print("Starting calibration...")
result = calibrate_world(cfg, preset='medium', seed=1000, tag='medium_overrides')
print("Calibration finished.")

# Extract and print
b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']
b1_in_target = result['b1_in_target_band']

print(f"B1 recall_at_1pct_fpr_val: {b1_recall}")
print(f"Fraud share: {fraud_share}")
print(f"b1_in_target_band: {b1_in_target}")