"""Event, decision and ring schemas.

Two layers:

* slim ``__slots__`` classes (``Event``, ``LabeledEvent``) used on the hot path by the engine, replay and
  evaluation; ``Event`` has no label fields and ``LabeledEvent`` is *not* an ``Event``;
* pydantic models (``EventIn``, ``DecisionOut``, ``RingAlert`` ...) used to validate API input and to document
  and test the API payloads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_TYPES: tuple[str, ...] = ("TRANSFER", "PAYMENT", "CASH_OUT", "CASH_IN", "DEBIT")
TYPE_CODE: dict[str, int] = {t: i for i, t in enumerate(EVENT_TYPES)}
TIERS: tuple[str, ...] = ("allow", "verify", "hold")
ROLES: tuple[str, ...] = ("victim", "mule", "collector", "sink", "legit")
LABEL_FIELDS: tuple[str, ...] = ("is_fraud", "ring_id", "hop", "src_role", "dst_role")

EventType = Literal["TRANSFER", "PAYMENT", "CASH_OUT", "CASH_IN", "DEBIT"]
Tier = Literal["allow", "verify", "hold"]


@dataclass(slots=True)
class Event:
    """One payment as the engine sees it. No label fields, by design (rule L9)."""

    event_id: int
    ts: float
    src: str
    dst: str
    amount: float
    type: str
    src_bal_before: Optional[float] = None
    src_bal_after: Optional[float] = None
    dst_bal_before: Optional[float] = None
    dst_bal_after: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "src": self.src,
            "dst": self.dst,
            "amount": self.amount,
            "type": self.type,
            "src_bal_before": self.src_bal_before,
            "src_bal_after": self.src_bal_after,
            "dst_bal_before": self.dst_bal_before,
            "dst_bal_after": self.dst_bal_after,
        }


@dataclass(slots=True)
class LabeledEvent:
    """An event plus ground truth. Used by replay, evaluation and the UI overlay only.

    Deliberately not a subclass of ``Event``: the engine rejects it.
    """

    event: Event
    is_fraud: int = 0
    ring_id: str = "-1"
    hop: int = -1
    src_role: str = "legit"
    dst_role: str = "legit"

    def truth(self, injected: bool = False) -> dict:
        return {
            "is_fraud": self.is_fraud,
            "ring_id": self.ring_id,
            "hop": self.hop,
            "src_role": self.src_role,
            "dst_role": self.dst_role,
            "injected": injected,
        }


class EventIn(BaseModel):
    """API input for one transaction. Label fields and unknown keys are rejected (422)."""

    model_config = ConfigDict(extra="forbid")

    event_id: int
    ts: float
    src: str = Field(min_length=1, max_length=64)
    dst: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, lt=1e9)
    type: EventType
    src_bal_before: Optional[float] = None
    src_bal_after: Optional[float] = None
    dst_bal_before: Optional[float] = None
    dst_bal_after: Optional[float] = None

    @field_validator("ts", "src_bal_before", "src_bal_after", "dst_bal_before", "dst_bal_after")
    @classmethod
    def _finite(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v

    def to_event(self) -> Event:
        return Event(
            self.event_id,
            float(self.ts),
            self.src,
            self.dst,
            float(self.amount),
            self.type,
            self.src_bal_before,
            self.src_bal_after,
            self.dst_bal_before,
            self.dst_bal_after,
        )


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[dict]


class ReasonOut(BaseModel):
    code: str
    text: str
    evidence: dict[str, float] = {}


class StageLatency(BaseModel):
    features: int
    model: int
    decision: int
    total: int


class ShadowOut(BaseModel):
    score: float
    decision: Tier


class DecisionOut(BaseModel):
    event_id: int
    decision: Tier
    score: float
    base_score: float
    risk: int
    detector: str
    reasons: list[ReasonOut]
    ring_id: Optional[str] = None
    ring_score: Optional[float] = None
    recommended_action: str
    latency_us: StageLatency
    model_version: str
    shadow: Optional[dict[str, ShadowOut]] = None


class RingAccount(BaseModel):
    id: str
    role: Literal["entry", "relay", "exit"]
    in_amount: float
    out_amount: float
    first_ts: float


class RingEdge(BaseModel):
    event_id: int
    ts: float
    src: str
    dst: str
    amount: float
    type: EventType
    score: float


class RingAlert(BaseModel):
    ring_id: str
    status: Literal["candidate", "open", "alert", "closed"]
    first_ts: float
    last_ts: float
    alert_ts: Optional[float] = None
    ring_score: float
    peak_score: float
    accounts: list[RingAccount]
    edges: list[RingEdge]
    n_accounts: int
    n_edges: int
    n_relays: int
    inflow: float
    outflow: float
    exit_amount: float
    longest_chain: int
    reasons: list[ReasonOut]
    merged_from: list[str] = []
    truth: Optional[dict] = None


class RingEventOut(BaseModel):
    kind: Literal["create", "update", "merge", "alert", "close"]
    ts: float
    ring: RingAlert


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorBody
