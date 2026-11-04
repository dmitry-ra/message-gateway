"""One in-memory queue per destination, shared by all interfaces."""

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from message_gateway.core.adapter import Adapter
from message_gateway.core.models import DeliveryBusy, JsonValue, Message, Readiness

logger = logging.getLogger(__name__)


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
            if len(sent_at) > 64 or datetime.fromisoformat(sent_at).utcoffset() is None:
                raise ValueError("sent_at must be an ISO 8601 timestamp with a timezone")
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
        await asyncio.gather(*(self._dispatch(target) for target in self.targets.values()))

    async def _dispatch(self, target: Target) -> None:
        if target.lock.locked():
            return
        async with target.lock:
            try:
                target.state = await target.adapter.readiness()
            except Exception:
                target.state = Readiness.UNKNOWN
                logger.warning("Destination readiness check failed", exc_info=True)
                return
            if target.state != Readiness.READY or not target.queue:
                return
            count = min(len(target.queue), self.batch_limit if target.batching else 1)
            messages = [target.queue.popleft() for _ in range(count)]
            target.sending = count
            ids = [message.id for message in messages]
            try:
                receipt = await target.adapter.send(messages)
                target.last_result = {"status": "accepted", "messages": ids, "receipt": receipt}
                logger.info("Delivery accepted: messages=%s receipt=%s", ids, receipt)
            except DeliveryBusy:
                target.queue.extendleft(reversed(messages))
                target.state = Readiness.BUSY
            except asyncio.CancelledError:
                target.last_result = {"status": "uncertain", "messages": ids, "error": "Stopped"}
                raise
            except Exception as error:
                target.last_result = {"status": "uncertain", "messages": ids, "error": str(error)}
                logger.warning("Delivery result unknown; batch will not be retried", exc_info=True)
            finally:
                target.sending = 0

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
