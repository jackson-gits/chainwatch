import json
from chainwatch.config import Config, _to_section
from chainwatch.eval.protocol import calibrate_world

# Load best_medium_config.json
with open('best_medium_config.json', 'r') as f:
    config_dict = json.load(f)

# Create Config object
cfg = Config(_to_section(config_dict))

# Run calibration
result = calibrate_world(cfg, preset='medium', seed=1000, tag='test_calibration')

# Extract B1 recall@1%FPR and fraud share
b1_recall = result['detectors']['B1']['recall_at_1pct_fpr_val']
fraud_share = result['fraud_share']

print(f"B1 recall@1%FPR: {b1_recall}")
print(f"Fraud share: {fraud_share}")