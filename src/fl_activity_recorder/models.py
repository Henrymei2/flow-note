from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Confidence = Literal["exact", "state_diff", "signal_only"]


@dataclass(slots=True)
class ActivityEvent:
    category: str
    action: str
    summary: str
    confidence: Confidence
    entity_type: str | None = None
    entity_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ActivityEvent:
        confidence = payload.get("confidence", "signal_only")
        if confidence not in {"exact", "state_diff", "signal_only"}:
            confidence = "signal_only"
        return cls(
            category=str(payload.get("category", "system")),
            action=str(payload.get("action", "unknown")),
            summary=str(payload.get("summary", "FL Studio activity")),
            confidence=confidence,
            entity_type=_optional_string(payload.get("entity_type")),
            entity_key=_optional_string(payload.get("entity_key")),
            payload=dict(payload.get("payload") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)
