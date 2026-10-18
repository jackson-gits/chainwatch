import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting generate test...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.data.world.generate import generate_world

print("Loaded imports")
sys.stdout.flush()

cfg = load(preset='small')
print("Loaded small config")
sys.stdout.flush()

overrides = {
    'world.noise_level': 2.0,
    'world.hard_negative_boost': 20.0,
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
cfg = cfg.with_overrides(overrides)
print("Applied overrides")
sys.stdout.flush()

out_dir = r'D:\projects\chainwatch\data\processed\test_world'
print(f"Generating world to {out_dir}")
sys.stdout.flush()
rep = generate_world(cfg, seed=1000, out_dir=out_dir, progress=False)
print("Generated world")
sys.stdout.flush()
print(f"Report keys: {rep.keys()}")
sys.stdout.flush()