"""Shared data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

AlertDirection = Literal["above", "below"]
AlertStatus = Literal["PENDING", "FIRING", "FIRED", "CANCELLED"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(value: datetime | None = None) -> str:
    resolved = value or utc_now()
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat()


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class PriceQuote:
    instrument: str
    bid: float
    ask: float
    time: datetime
    tradeable: bool = True
    source: str = "unknown"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["time"] = self.time.isoformat()
        payload["mid"] = self.mid
        return payload


@dataclass(frozen=True)
class Alert:
    id: int
    instrument: str
    target_price: float
    direction: AlertDirection
    status: AlertStatus
    note: str | None
    created_at: datetime
    updated_at: datetime
    fired_at: datetime | None = None
    trigger_price: float | None = None
    last_error: str | None = None

    def is_current(self) -> bool:
        return self.status in {"PENDING", "FIRING"}

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        payload["fired_at"] = self.fired_at.isoformat() if self.fired_at else None
        return payload

