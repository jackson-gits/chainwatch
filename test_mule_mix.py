import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting test mule_mix active=1.0...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.data.world.generate import generate_world

print("Loaded imports")
sys.stdout.flush()

cfg = load(preset='medium')
print("Loaded medium config")
sys.stdout.flush()

overrides = {
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
}
cfg = cfg.with_overrides(overrides)
print(f"Applied overrides: mule_mix={dict(cfg.world.mule_mix)}")
sys.stdout.flush()

out_dir = r'D:\projects\chainwatch\data\processed\test_mule_mix'
print(f"Generating world to {out_dir}")
sys.stdout.flush()
rep = generate_world(cfg, seed=1000, out_dir=out_dir, progress=False)
print("Generated world")
sys.stdout.flush()
print(f"Report keys: {rep.keys()}")
sys.stdout.flush()
print(f"Number of events: {rep['n_events']}")
sys.stdout.flush()