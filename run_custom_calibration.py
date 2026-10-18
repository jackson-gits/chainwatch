from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

# Load base config for preset medium
cfg = load(preset='medium')

# Apply overrides
overrides = {
    'world.noise_level': 2.0,
    'world.hard_negative_boost': 20.0,
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 200,
}
cfg = cfg.with_overrides(overrides)

# Run calibration
result = calibrate_world(cfg, preset='medium', seed=1000, tag='custom_overrides')

# Extract and print
b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']

print(f"B1 recall@1%FPR: {b1_recall}")
print(f"Fraud share: {fraud_share}")