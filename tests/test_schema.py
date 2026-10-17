"""Spec 17.2 (test_schema): the engine input type carries no labels and rejects bad input.

Rule D1: every account id in fixtures is an opaque string.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from chainwatch.schema import EVENT_TYPES, LABEL_FIELDS, Event, EventIn, LabeledEvent

VALID = {
    "event_id": 1,
    "ts": 12.5,
    "src": "A00000001",
    "dst": "A00000002",
    "amount": 10.0,
    "type": "TRANSFER",
}


def test_event_types_constant() -> None:
    assert EVENT_TYPES == ("TRANSFER", "PAYMENT", "CASH_OUT", "CASH_IN", "DEBIT")


def test_eventin_accepts_valid_payload_and_maps_to_event() -> None:
    ei = EventIn(**VALID, src_bal_before=100.0, src_bal_after=90.0)
    ev = ei.to_event()
    assert isinstance(ev, Event)
    assert ev.event_id == 1
    assert ev.ts == 12.5
    assert ev.src == "A00000001"
    assert ev.dst == "A00000002"
    assert ev.amount == 10.0
    assert ev.type == "TRANSFER"
    assert ev.src_bal_before == 100.0
    assert ev.dst_bal_before is None  # optional balances default to null


def test_eventin_rejects_unknown_extra_key() -> None:
    with pytest.raises(ValidationError):
        EventIn(**VALID, note="hello")


@pytest.mark.parametrize("field", LABEL_FIELDS)
def test_eventin_rejects_label_fields(field: str) -> None:
    """Label fields are ground truth only; the API input type must refuse them (spec 8.1)."""
    with pytest.raises(ValidationError):
        EventIn(**{**VALID, field: 1})


@pytest.mark.parametrize(
    "patch",
    [
        {"ts": "soon"},
        {"amount": "10 bucks"},
        {"event_id": "one"},
        {"type": "WIRE"},
        {"type": "cash_out"},
        {"src": 123},
        {"dst": ""},
    ],
)
def test_eventin_rejects_bad_types(patch: dict) -> None:
    with pytest.raises(ValidationError):
        EventIn(**{**VALID, **patch})


@pytest.mark.parametrize("amount", [0.0, -1.0, 1e9, 1e12])
def test_eventin_rejects_non_positive_or_capped_amounts(amount: float) -> None:
    with pytest.raises(ValidationError):
        EventIn(**{**VALID, "amount": amount})


def test_eventin_rejects_long_account_id() -> None:
    with pytest.raises(ValidationError):
        EventIn(**{**VALID, "src": "A" * 65})


@pytest.mark.parametrize("field", ["ts", "src_bal_before", "dst_bal_after"])
def test_eventin_rejects_non_finite_numbers(field: str) -> None:
    for bad in (math.inf, math.nan):
        with pytest.raises(ValidationError):
            EventIn(**{**VALID, field: bad})


# --- the slim engine input type (Event) has no label machinery -------------------------------


def test_labeled_event_is_not_an_event() -> None:
    ev = Event(1, 0.0, "A00000001", "A00000002", 5.0, "TRANSFER")
    le = LabeledEvent(ev, is_fraud=1, ring_id="2", hop=0)
    assert not isinstance(le, Event)
    assert LabeledEvent not in Event.__mro__
    assert isinstance(le.event, Event)


def test_event_has_no_label_slots() -> None:
    assert "is_fraud" not in Event.__slots__
    assert "ring_id" not in Event.__slots__
    ev = Event(1, 0.0, "A00000001", "A00000002", 5.0, "TRANSFER")
    with pytest.raises(AttributeError):
        ev.is_fraud = 1  # type: ignore[attr-defined]


def test_event_rejects_label_kwargs() -> None:
    with pytest.raises(TypeError):
        Event(1, 0.0, "A00000001", "A00000002", 5.0, "TRANSFER", is_fraud=1)


def test_event_to_dict_has_exactly_the_event_fields() -> None:
    ev = Event(1, 0.0, "A00000001", "A00000002", 5.0, "TRANSFER")
    assert set(ev.to_dict()) == {
        "event_id",
        "ts",
        "src",
        "dst",
        "amount",
        "type",
        "src_bal_before",
        "src_bal_after",
        "dst_bal_before",
        "dst_bal_after",
    }


def test_labeled_event_defaults_and_truth() -> None:
    ev = Event(1, 0.0, "A00000001", "A00000002", 5.0, "TRANSFER")
    le = LabeledEvent(ev)
    assert (le.is_fraud, le.ring_id, le.hop, le.src_role, le.dst_role) == (0, "-1", -1, "legit", "legit")
    truth = le.truth(injected=True)
    assert truth["is_fraud"] == 0
    assert truth["injected"] is True
