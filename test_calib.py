from chainwatch.config import load
from chainwatch.eval.protocol import calibrate_world

print("Loading config...")
cfg = load(preset='medium')
print("Config loaded.")

overrides = {
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
print("Applying overrides...")
cfg = cfg.with_overrides(overrides)
print("Overrides applied.")
print("Starting calibration...")
result = calibrate_world(cfg, preset='medium', seed=1000, tag='test')
print("Calibration finished.")
print(result.keys())