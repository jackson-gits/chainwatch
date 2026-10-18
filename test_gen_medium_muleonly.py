import sys
import time
from chainwatch.config import load
from chainwatch.data.world.generate import generate_world

flush_print = lambda *args, **kwargs: (print(*args, **kwargs), sys.stdout.flush())

flush_print("Loading medium preset...")
cfg = load(preset='medium')
flush_print("Preset loaded.")

overrides = {
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
}
cfg = cfg.with_overrides(overrides)
flush_print(f"Overrides applied. n_campaigns = {cfg.world.n_campaigns}, noise_level={cfg.world.noise_level}, hard_negative_boost={cfg.world.hard_negative_boost}")

flush_print("Generating world...")
start = time.time()
rep = generate_world(cfg, seed=1000, out_dir='data/test_world_medium_muleonly')
flush_print(f"World generated in {time.time()-start:.2f} seconds")
flush_print(f"Fraud share: {rep['fraud_share']}")
flush_print(f"Number of events: {rep['n_events']}")