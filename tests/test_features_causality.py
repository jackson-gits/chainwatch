"""Causality of the streaming feature engine (spec 9.1: features see strictly earlier events).

The invariants pinned here:

* a feature vector for event *i* depends only on events ``0..i`` (prefix equals full-stream run);
* rewriting, deleting, reordering or appending events after index *i* leaves every earlier vector
  bit-for-bit unchanged, while the rewritten event's own vector does change;
* the engine never emits NaN or infinity, even on adversarial amounts, missing balances and equal or
  out-of-order timestamps, and it rejects labeled input (rule L9).
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

from chainwatch.data.io import iter_events
from chainwatch.features.engine import FeatureEngine
from chainwatch.features.registry import ALL_NAMES
from chainwatch.schema import Event, LabeledEvent

TYPES = ("TRANSFER", "PAYMENT", "CASH_OUT", "CASH_IN", "DEBIT")


def _stream(seed: int = 5, n: int = 240, n_accts: int = 20) -> list[Event]:
    rnd = random.Random(seed)
    ids = [f"acct{i:03d}" for i in range(n_accts)]
    events = []
    ts = 0.0
    for i in range(1, n + 1):
        ts += rnd.choice([0.0, 1.0, 5.0, 60.0, 600.0, 3600.0, 4000.0])
        src = rnd.choice(ids)
        dst = rnd.choice([a for a in ids if a != src])
        amount = rnd.choice([rnd.uniform(1.0, 100.0), 1000.0 * rnd.randint(1, 90), 49999.0])
        events.append(
            Event(
                i,
                ts,
                src,
                dst,
                amount,
                rnd.choice(TYPES),
                rnd.choice([None, rnd.uniform(0.0, 300_000.0)]),
                rnd.choice([None, 0.0, rnd.uniform(0.0, 50_000.0)]),
                None,
                None,
            )
        )
    return events


def _run(cfg, events) -> list[dict[str, float]]:
    eng = FeatureEngine(cfg, ("G2",))
    return [eng.step(ev) for ev in events]


def test_prefix_equals_full_stream_run(tiny_cfg_session):
    stream = _stream(seed=5)
    full = _run(tiny_cfg_session, stream)
    for k in (1, 17, 60, 121, len(stream)):
        prefix = _run(tiny_cfg_session, stream[:k])
        assert prefix == full[:k], f"prefix {k} differs from the full run"


def test_rewriting_later_events_does_not_change_earlier_features(tiny_cfg_session):
    stream = _stream(seed=6, n=200)
    full = _run(tiny_cfg_session, stream)
    k = 80
    modified = list(stream)
    for j in range(k, len(modified)):
        ev = modified[j]
        modified[j] = Event(
            ev.event_id,
            ev.ts + 10_000.0 * (j - k + 1),  # push every later event far into the future
            f"zzz{j % 7:02d}",
            f"yyy{j % 5:02d}",
            ev.amount * 3.0,
            "CASH_OUT",
            123.0,
            0.5,
            None,
            None,
        )
    after = _run(tiny_cfg_session, modified)
    assert after[:k] == full[:k]
    # the rewritten events themselves are genuinely different (the edit was not a no-op)
    assert after[k:] != full[k:]


def test_deleting_or_reordering_later_events_does_not_change_earlier_features(tiny_cfg_session):
    stream = _stream(seed=8, n=180)
    k = 70
    full = _run(tiny_cfg_session, stream)
    assert _run(tiny_cfg_session, stream[:k] + stream[3 * k :])[:k] == full[:k]
    tail = stream[k:]
    rotated = stream[:k] + tail[len(tail) // 2 :] + tail[: len(tail) // 2]
    assert _run(tiny_cfg_session, rotated)[:k] == full[:k]
    assert _run(tiny_cfg_session, stream[:k] + list(reversed(tail)))[:k] == full[:k]


def test_editing_one_event_changes_only_that_event_and_later(tiny_cfg_session):
    stream = _stream(seed=7, n=120)
    full = _run(tiny_cfg_session, stream)
    k = 50
    modified = list(stream)
    ev = modified[k]
    modified[k] = Event(
        ev.event_id, ev.ts, ev.src, ev.dst, ev.amount + 777.0, ev.type, None, None, None, None
    )
    after = _run(tiny_cfg_session, modified)
    assert after[:k] == full[:k]
    assert after[k]["t_amount"] == ev.amount + 777.0
    assert after[k] != full[k]


def test_all_features_are_finite(tiny_cfg_session):
    rnd = random.Random(99)
    events = []
    for i in range(1, 301):
        events.append(
            Event(
                i,
                float(i) * rnd.choice([0.0, 1.0, 900.0, 90_000.0]),
                f"s{rnd.randrange(12):02d}",
                f"d{rnd.randrange(12):02d}",
                rnd.choice([1e-3, 1.0, 999.99, 1e6, 4.9e8]),
                rnd.choice(TYPES),
                rnd.choice([None, 0.0, 1e-9, 1.0, 1e9]),
                rnd.choice([None, 0.0, 1e-9, 1.0]),
                None,
                None,
            )
        )
    for f in _run(tiny_cfg_session, events):
        assert all(math.isfinite(v) for v in f.values())
        assert all(v >= 0.0 for v in f.values())


def test_equal_timestamps_do_not_break_causality(tiny_cfg_session):
    """All events share one timestamp: order is the stream order, and prefixes still match."""
    stream = [
        Event(
            i,
            1000.0,
            f"acct{i % 4:02d}",
            f"acct{(i + 1) % 4:02d}",
            100.0 + i,
            "TRANSFER",
            None,
            None,
            None,
            None,
        )
        for i in range(1, 41)
    ]
    full = _run(tiny_cfg_session, stream)
    for k in (5, 20, 40):
        assert _run(tiny_cfg_session, stream[:k]) == full[:k]


def test_engine_rejects_labeled_events(tiny_cfg_session):
    """Ground truth must never reach the engine (rule L9): LabeledEvent is not an Event."""
    eng = FeatureEngine(tiny_cfg_session, ("G2",))
    ev = Event(1, 1000.0, "A00000001", "A00000002", 500.0, "TRANSFER", None, None, None, None)
    with pytest.raises(TypeError):
        eng.step(LabeledEvent(ev, is_fraud=1, ring_id="7", hop=2, src_role="mule", dst_role="sink"))
    assert all(math.isfinite(v) for v in eng.step(ev).values())


@pytest.mark.slow
def test_tiny_world_prefix_is_causal_and_finite(tiny_cfg_session, tiny_world):
    stream = list(
        itertools.islice((le.event for le in iter_events(tiny_world / "events.csv.gz")), 3000)
    )
    assert len(stream) == 3000
    full = _run(tiny_cfg_session, stream)
    for k in (1, 750, 3000):
        assert _run(tiny_cfg_session, stream[:k]) == full[:k]
    for f in full:
        assert set(f) == set(ALL_NAMES)
        assert all(math.isfinite(v) for v in f.values())
