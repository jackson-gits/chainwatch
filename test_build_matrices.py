import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting test build_matrices...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.data.world.generate import generate_world
from chainwatch.features.matrix import build_matrices
from pathlib import Path

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
    'world.n_customers': 1000,
    'world.n_merchants': 50,
    'world.n_traders': 10,
    'world.n_salary_payers': 5,
    'world.n_landlords': 50,
    'world.n_agents': 10,
}
cfg = cfg.with_overrides(overrides)
print(f"Applied overrides: n_customers={cfg.world.n_customers}")
sys.stdout.flush()

# Generate world
out_dir = r'D:\projects\chainwatch\data\processed\test_world_for_matrix'
print(f"Generating world to {out_dir}")
sys.stdout.flush()
rep = generate_world(cfg, seed=1000, out_dir=out_dir, progress=False)
print("Generated world")
sys.stdout.flush()
print(f"Number of events: {rep['n_events']}")
sys.stdout.flush()

# Build matrices
feat_dir = Path(r'D:\projects\chainwatch\data\processed\test_features_for_matrix')
print(f"Building matrices to {feat_dir}")
sys.stdout.flush()
build_matrices(cfg, Path(out_dir), feat_dir)
print("Built matrices")
sys.stdout.flush()
# Check meta
meta_path = feat_dir / 'meta.json'
if meta_path.exists():
    import json
    meta = json.loads(meta_path.read_text())
    print(f"Meta keys: {meta.keys()}")
    print(f"Feature names length: {len(meta.get('feature_names', []))}")
sys.stdout.flush()