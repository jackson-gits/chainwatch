import sys
from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

# Ensure line-buffered output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
else:
    # Python < 3.7, use unbuffered? We'll flush manually.
    pass

def flush_print(*args, **kwargs):
    print(*args, **kwargs)
    sys.stdout.flush()

# Load base config for preset medium
flush_print("Loading medium preset...")
base_cfg = load(preset='medium')
flush_print("Preset loaded.")

# Set constant overrides for mule_mix and n_campaigns
constant_overrides = {
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
base_cfg = base_cfg.with_overrides(constant_overrides)
flush_print("Constant overrides applied (mule_mix active=1.0, n_campaigns=300).")

# Iterate over noise_level and hard_negative_boost
flush_print("Starting grid search...")
for noise_level in [2.0, 2.5, 3.0]:
    for hard_negative_boost in [20.0, 25.0, 30.0]:
        overrides = {
            'world.noise_level': noise_level,
            'world.hard_negative_boost': hard_negative_boost,
        }
        cfg = base_cfg.with_overrides(overrides)
        flush_print(f"\nRunning noise_level={noise_level}, hard_negative_boost={hard_negative_boost}")
        # Run calibration
        result = calibrate_world(cfg, preset='medium', seed=1000, tag=f'noise_{noise_level}_hnb_{hard_negative_boost}')
        # Extract metrics
        b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
        fraud_share = result['fraud_share']
        # Print results
        flush_print(f"Result: noise_level={noise_level}, hard_negative_boost={hard_negative_boost}, B1 recall@1%FPR={b1_recall:.6f}, fraud share={fraud_share:.6f}")

flush_print("Grid search completed.")