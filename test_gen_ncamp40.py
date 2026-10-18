import sys
import os
sys.stdout.flush()
sys.stderr.flush()

print("Starting test generate with n_campaigns=40...")
sys.stdout.flush()

from chainwatch.config import load
from chainwatch.data.world.generate import generate_world

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
    'world.n_campaigns': 40,
    'world.n_customers': 1000,
    'world.n_merchants': 50,
    'world.n_traders': 10,
    'world.n_salary_payers': 5,
    'world.n_landlords': 50,
    'world.n_agents': 10,
}
cfg = cfg.with_overrides(overrides)
print(f"Applied overrides: n_campaigns={cfg.world.n_campaigns}, n_customers={cfg.world.n_customers}")
sys.stdout.flush()

out_dir = r'D:\projects\chainwatch\data\processed\test_gen_ncamp40'
print(f"Generating world to {out_dir}")
sys.stdout.flush()
rep = generate_world(cfg, seed=1000, out_dir=out_dir, progress=False)
print("Generated world")
sys.stdout.flush()
print(f"Report keys: {rep.keys()}")
sys.stdout.flush()
print(f"Number of events: {rep['n_events']}")
sys.stdout.flush()