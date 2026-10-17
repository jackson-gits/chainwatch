"""Shared tiny-world definition for the test suite (spec 17.1).

Stdlib-only on purpose: serving tests import this in an environment without numpy, pandas or
LightGBM. The numeric knobs live in :data:`TINY_OVERRIDES` so every consumer (train-env conftest,
serve-env conftest, committed mini model package) builds the identical config, which the feature
registry hash check in ``ScoringEngine`` requires.
"""

from __future__ import annotations

from chainwatch.config import Config, load

#: world/eval/model overrides that define the tiny test world (spec 17.1)
TINY_OVERRIDES: dict = {
    "world.preset": "tiny",
    "world.n_customers": 1500,
    "world.n_merchants": 60,
    "world.n_traders": 10,
    "world.n_salary_payers": 4,
    "world.n_landlords": 30,
    "world.n_agents": 12,
    "world.n_campaigns": 12,
    "world.days": 8,
    "world.campaign_start_min_day": 1.0,
    "eval.burn_in_days": 1,
    "eval.split_days": [4, 6, 8],
    "model.min_train_positives": 10,
    "model.n_estimators": 40,
    "model.early_stopping_rounds": 10,
    "model.n_jobs": 1,
}

TINY_SEED = 7

#: the committed model package built from the tiny world (tests/fixtures/mini_models)
MINI_MODELS_DIR = "tests/fixtures/mini_models"


def tiny_cfg() -> Config:
    """The tiny config every test fixture must share."""
    return load(preset=None, overrides=dict(TINY_OVERRIDES))
