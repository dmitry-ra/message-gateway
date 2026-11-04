"""Values shared by the service and adapters."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias
from uuid import uuid4

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class Readiness(StrEnum):
    READY = "ready"
    BUSY = "busy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Message:
    id: str
    channel: str
    content: JsonValue
    received_at: str
    sender: str | None = None
    preview: str | None = None
    sent_at: str | None = None

    @classmethod
    def create(
        cls,
        channel: str,
        content: JsonValue,
        *,
        sender: str | None = None,
        preview: str | None = None,
        sent_at: str | None = None,
    ) -> "Message":
        return cls(
            uuid4().hex, channel, content, datetime.now(UTC).isoformat(), sender, preview, sent_at
        )


class DeliveryBusy(Exception):
    """The receiver explicitly refused the batch without accepting it."""


class DeliveryUnknown(Exception):
    """The receiver may have accepted the batch; do not retry automatically."""
