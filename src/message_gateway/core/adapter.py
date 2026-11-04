"""The minimal contract implemented by destinations."""

from typing import Protocol

from message_gateway.core.models import Message, Readiness


class Adapter(Protocol):
    async def readiness(self) -> Readiness: ...

    async def send(self, messages: list[Message]) -> str:
        """Return a transport receipt; raise DeliveryBusy only on definite refusal."""
        ...

    async def close(self) -> None: ...
