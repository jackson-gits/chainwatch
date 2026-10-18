import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting test n_campaigns=200...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.data.world.generate import generate_world

print("Loaded imports")
sys.stdout.flush()

cfg = load(preset='medium')
print("Loaded medium config")
sys.stdout.flush()

overrides = {
    'world.n_campaigns': 200,
}
cfg = cfg.with_overrides(overrides)
print(f"Applied overrides: n_campaigns={cfg.world.n_campaigns}")
sys.stdout.flush()

out_dir = r'D:\projects\chainwatch\data\processed\test_ncampaigns200'
print(f"Generating world to {out_dir}")
sys.stdout.flush()
rep = generate_world(cfg, seed=1000, out_dir=out_dir, progress=False)
print("Generated world")
sys.stdout.flush()
print(f"Report keys: {rep.keys()}")
sys.stdout.flush()
print(f"Number of events: {rep['n_events']}")
sys.stdout.flush()