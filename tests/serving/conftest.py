"""Serve-only fixtures (spec 17.1).

Runs with ONLY ``requirements-serve.txt`` plus pytest and httpx: no numpy, LightGBM or sklearn is
ever imported here. Models come from the committed package ``tests/fixtures/mini_models``, which
was trained on the tiny world defined in ``tests/tiny.py`` (stdlib-only module).
"""

from __future__ import annotations

import pytest

from tests.tiny import MINI_MODELS_DIR, tiny_cfg


@pytest.fixture(scope="session")
def mini_cfg():
    """The exact config the committed mini models were trained with."""
    return tiny_cfg()


@pytest.fixture(scope="session")
def mini_engine(mini_cfg):
    """ScoringEngine over the committed tiny model package."""
    from chainwatch.serving.engine import ScoringEngine

    return ScoringEngine(mini_cfg, MINI_MODELS_DIR)


@pytest.fixture(scope="session")
def mini_client(mini_cfg):
    """FastAPI TestClient over the committed package, replay off."""
    from fastapi.testclient import TestClient

    from chainwatch.serving.app import create_app

    app = create_app(model_dir=MINI_MODELS_DIR, replay=False, cfg=mini_cfg)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def make_events():
    """Factory for hand-made ``EventIn`` payloads (no real data, rule D1)."""

    from chainwatch.schema import EventIn

    def _make(n: int, *, ts0: float = 500_000.0, src="A00000001", dst="A00000002", amount=1000.0):
        return [
            EventIn(
                event_id=i,
                ts=ts0 + i,
                type="TRANSFER",
                src=src,
                dst=dst,
                amount=amount,
            )
            for i in range(n)
        ]

    return _make
