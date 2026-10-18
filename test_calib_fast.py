import json
import time
from chainwatch.config import Config, _to_section
from chainwatch.eval.protocol import calibrate_world

with open('best_medium_config.json', 'r') as f:
    config_dict = json.load(f)

# Override to speed up
config_dict['model']['n_estimators'] = 10
config_dict['model']['early_stopping_rounds'] = 2
config_dict['model']['neg_downsample'] = 0.5  # maybe

cfg = Config(_to_section(config_dict))

print("Starting calibration for seed 1000 with reduced estimators...", flush=True)
start = time.time()
result = calibrate_world(cfg, preset='medium', seed=1000, tag='test_fast')
print(f"Calibration took {time.time() - start:.2f} seconds", flush=True)
print("B1 recall@1%FPR:", result['detectors']['B1']['recall_at_1pct_fpr_val'], flush=True)
print("Fraud share:", result['fraud_share'], flush=True)