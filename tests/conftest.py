"""Session fixtures for the full test suite (spec 17.1).

Heavy imports (numpy, LightGBM, FastAPI) happen inside fixture bodies, never at module import:
``tests/serving/`` runs in an environment with only ``requirements-serve.txt``, and pytest imports
this conftest for those tests too.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.tiny import TINY_SEED, tiny_cfg


@pytest.fixture(scope="session")
def tiny_cfg_session():
    """Session-wide tiny config (spec 17.1)."""
    return tiny_cfg()


@pytest.fixture(scope="session")
def tiny_world(tmp_path_factory, tiny_cfg_session):
    """Tiny world generated once: events.csv.gz, accounts.csv.gz, rings.json, manifests."""
    from chainwatch.data.world.generate import generate_world

    wd = tmp_path_factory.mktemp("world") / "world_tiny_seed7"
    generate_world(tiny_cfg_session, TINY_SEED, wd, progress=False)
    yield wd
    shutil.rmtree(wd.parent, ignore_errors=True)


@pytest.fixture(scope="session")
def tiny_features(tmp_path_factory, tiny_cfg_session, tiny_world):
    """Feature matrices replayed through the one true FeatureEngine (rule E4)."""
    from chainwatch.features.matrix import build_matrices

    fd = tmp_path_factory.mktemp("features") / "features_tiny"
    build_matrices(tiny_cfg_session, tiny_world, fd)
    return fd


@pytest.fixture(scope="session")
def tiny_models(tmp_path_factory, tiny_cfg_session, tiny_features):
    """B0, B1 and G2 trained on the tiny world (40 trees) plus thresholds and profile."""
    from chainwatch.model.thresholds import compute_thresholds
    from chainwatch.model.train import train_detector

    md = tmp_path_factory.mktemp("models") / "tiny"
    for detector in ("B0", "B1", "G2"):
        train_detector(tiny_cfg_session, tiny_features, detector, md, seed=TINY_SEED)
    compute_thresholds(tiny_cfg_session, tiny_features, md)
    return md


@pytest.fixture(scope="session")
def engine(tiny_cfg_session, tiny_models):
    """A live ScoringEngine over the tiny models (production detector G2+R)."""
    from chainwatch.serving.engine import ScoringEngine

    return ScoringEngine(tiny_cfg_session, tiny_models)


@pytest.fixture(scope="session")
def client(tiny_cfg_session, tiny_models):
    """FastAPI TestClient over the tiny engine, replay off."""
    from fastapi.testclient import TestClient

    from chainwatch.serving.app import create_app

    app = create_app(model_dir=tiny_models, replay=False, cfg=tiny_cfg_session)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def tmp_workdir(tmp_path_factory):
    """A session-wide scratch directory for tests that write files."""
    return Path(tmp_path_factory.mktemp("work"))
