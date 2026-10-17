#!/usr/bin/env python3
"""Output the proposed best parameters for medium preset meeting constraints."""
from chainwatch.config import load
import json

# Base medium preset
cfg = load(preset="medium")
overrides = {
    "world.noise_level": 2.0,
    "world.hard_negative_boost": 20.0,
    "world.mule_mix.new": 0.0,
    "world.mule_mix.dormant": 0.0,
    "world.mule_mix.active": 1.0,
    "world.n_campaigns": 300,
}
cfg = cfg.with_overrides(overrides)

print("Proposed parameters for medium preset:")
print(f"  noise_level: {cfg.world.noise_level}")
print(f"  hard_negative_boost: {cfg.world.hard_negative_boost}")
print(f"  mule_mix: {cfg.world.mule_mix}")
print(f"  n_campaigns: {cfg.world.n_campaigns}")
print(f"  fraud_target_share: {cfg.world.fraud_target_share}")

# Save config
with open("best_medium_config.json", "w") as f:
    json.dump(cfg.to_dict(), f, indent=2)
print("\nFull configuration saved to best_medium_config.json")
print("(This configuration is based on extrapolation from existing calibration runs.)")