import sys
from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

# Load base config for preset medium
base_cfg = load(preset='medium')

# Set constant overrides for mule_mix and n_campaigns
constant_overrides = {
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
base_cfg = base_cfg.with_overrides(constant_overrides)

noise_level = 2.0
hard_negative_boost = 20.0
overrides = {
    'world.noise_level': noise_level,
    'world.hard_negative_boost': hard_negative_boost,
}
cfg = base_cfg.with_overrides(overrides)
print("Starting calibration...")
sys.stdout.flush()
try:
    # Run calibration
    result = calibrate_world(cfg, preset='medium', seed=1000, tag=f'noise_{noise_level}_hnb_{hard_negative_boost}')
    # Extract metrics
    b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
    fraud_share = result['fraud_share']
    # Print results
    print(f"Noise level: {noise_level}, Hard negative boost: {hard_negative_boost}, B1 recall@1%FPR: {b1_recall}, Fraud share: {fraud_share}")
    sys.stdout.flush()
except Exception as e:
    print(f"Error: {e}")
    sys.stdout.flush()