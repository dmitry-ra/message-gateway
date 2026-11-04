"""One in-memory queue per destination, shared by all interfaces."""

import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass, field

from pydantic import AwareDatetime, TypeAdapter

from message_gateway.core.adapter import Adapter
from message_gateway.core.models import DeliveryBusy, JsonValue, Message, Readiness
from message_gateway.diagnostics import emit

_TIMESTAMP_SHAPE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)
_TIMESTAMP = TypeAdapter(AwareDatetime)


class QueueFull(Exception):
    pass


@dataclass
class Target:
    adapter: Adapter
    batching: bool = True
    queue: deque[Message] = field(default_factory=deque)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: Readiness = Readiness.UNKNOWN
    sending: int = 0
    last_result: dict[str, object] | None = None
    observation: tuple[str, str, str | None, int, int, str | None] | None = None


class Service:
    def __init__(
        self, targets: dict[str, Target], *, queue_limit: int = 1000, batch_limit: int = 20
    ) -> None:
        if not targets or queue_limit < 1 or batch_limit < 1:
            raise ValueError("Targets and positive queue/batch limits are required")
        self.targets = targets
        self.queue_limit = queue_limit
        self.batch_limit = batch_limit
        self.wake = asyncio.Event()

    def submit(
        self,
        target: str,
        channel: str,
        content: JsonValue,
        *,
        sender: str | None = None,
        preview: str | None = None,
        sent_at: str | None = None,
    ) -> Message:
        destination = self.targets[target]
        if not channel.strip() or len(channel) > 128:
            raise ValueError("A channel of 1-128 nonblank characters is required")
        channel.encode("utf-8")
        if sender is not None:
            if not sender.strip() or len(sender) > 128:
                raise ValueError("Sender must contain 1-128 nonblank characters")
            sender.encode("utf-8")
        if preview is not None:
            if len(preview) > 256:
                raise ValueError("Preview cannot exceed 256 characters")
            preview.encode("utf-8")
        if sent_at is not None:
            if len(sent_at) > 64 or _TIMESTAMP_SHAPE.fullmatch(sent_at) is None:
                raise ValueError("sent_at must be an RFC 3339 timestamp with a timezone")
            _TIMESTAMP.validate_python(sent_at)
        if isinstance(content, str) and not content.strip():
            raise ValueError("Text content cannot be blank")
        encoded = json.dumps(content, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError("JSON-encoded content exceeds 65536 UTF-8 bytes")
        if len(destination.queue) + destination.sending >= self.queue_limit:
            raise QueueFull("Destination queue is full")
        message = Message.create(
            channel, json.loads(encoded), sender=sender, preview=preview, sent_at=sent_at
        )
        destination.queue.append(message)
        self.wake.set()
        return message

    def status(self) -> dict[str, object]:
        return {
            name: {
                "state": target.state.value,
                "batching": target.batching,
                "queued": len(target.queue),
                "sending": target.sending,
                "last_result": target.last_result,
            }
            for name, target in self.targets.items()
        }

    async def tick(self) -> None:
        await asyncio.gather(
            *(self._dispatch(name, target) for name, target in self.targets.items())
        )

    def _observe(
        self, name: str, target: Target, reason: str, error_type: str | None = None
    ) -> None:
        oldest = target.queue[0].received_at if target.queue else None
        observation = (
            target.state.value,
            reason,
            error_type,
            len(target.queue),
            target.sending,
            oldest,
        )
        if target.observation != observation:
            target.observation = observation
            emit(
                "destination_state",
                target=name,
                state=target.state.value,
                reason=reason,
                queued=len(target.queue),
                sending=target.sending,
                oldest_received_at=oldest,
                error_type=error_type,
            )

    async def _dispatch(self, name: str, target: Target) -> None:
        if target.lock.locked():
            return
        async with target.lock:
            try:
                target.state = await target.adapter.readiness()
            except Exception as error:
                target.state = Readiness.UNKNOWN
                self._observe(name, target, "readiness_error", type(error).__name__)
                return
            reason = "empty" if not target.queue else target.state.value
            self._observe(name, target, reason)
            if target.state != Readiness.READY or not target.queue:
                return
            count = min(len(target.queue), self.batch_limit if target.batching else 1)
            messages = [target.queue.popleft() for _ in range(count)]
            target.sending = count
            self._observe(name, target, "sending")
            ids = [message.id for message in messages]
            emit("delivery_start", target=name, messages=ids, queued=len(target.queue))
            try:
                receipt = await target.adapter.send(messages)
                target.last_result = {"status": "accepted", "messages": ids, "receipt": receipt}
                emit("delivery_accepted", target=name, messages=ids)
            except DeliveryBusy:
                target.queue.extendleft(reversed(messages))
                target.state = Readiness.BUSY
                emit("delivery_deferred", target=name, messages=ids, queued=len(target.queue))
            except asyncio.CancelledError:
                target.last_result = {"status": "uncertain", "messages": ids, "error": "Stopped"}
                emit("delivery_uncertain", target=name, messages=ids, error_type="CancelledError")
                raise
            except Exception as error:
                target.last_result = {"status": "uncertain", "messages": ids, "error": str(error)}
                emit(
                    "delivery_uncertain", target=name, messages=ids, error_type=type(error).__name__
                )
            finally:
                target.sending = 0
                self._observe(name, target, "empty" if not target.queue else target.state.value)

    async def run(self, interval: float = 0.25) -> None:
        if interval <= 0:
            raise ValueError("Polling interval must be positive")
        try:
            while True:
                self.wake.clear()
                await self.tick()
                try:
                    await asyncio.wait_for(self.wake.wait(), interval)
                except TimeoutError:
                    pass
        finally:
            await asyncio.gather(*(target.adapter.close() for target in self.targets.values()))
