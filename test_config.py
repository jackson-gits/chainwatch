from chainwatch.config import load

cfg = load(preset='medium')
print('Original noise_level:', cfg.world.noise_level)
print('Original hard_negative_boost:', cfg.world.hard_negative_boost)
print('Original mule_mix:', dict(cfg.world.mule_mix))
print('Original n_campaigns:', cfg.world.n_campaigns)

overrides = {
    'world.noise_level': 2.0,
    'world.hard_negative_boost': 20.0,
    'world.mule_mix.new': 0.0,
    'world.mule_mix.dormant': 0.0,
    'world.mule_mix.active': 1.0,
    'world.n_campaigns': 300,
}
cfg2 = cfg.with_overrides(overrides)
print('\nAfter overrides:')
print('noise_level:', cfg2.world.noise_level)
print('hard_negative_boost:', cfg2.world.hard_negative_boost)
print('mule_mix:', dict(cfg2.world.mule_mix))
print('n_campaigns:', cfg2.world.n_campaigns)