from chainwatch.config import load
from chainwatch.data.world.generate import generate_world
import time

cfg = load(preset='small')
# apply overrides for mule_mix and n_campaigns maybe?
overrides = {
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
cfg = cfg.with_overrides(overrides)
print("Generating world...")
start = time.time()
rep = generate_world(cfg, seed=1000, out_dir='data/test_world')
print(f"Generated in {time.time()-start:.2f} seconds")
print("Fraud share:", rep['fraud_share'])