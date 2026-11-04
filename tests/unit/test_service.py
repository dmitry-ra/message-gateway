import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import pytest

from message_gateway.core.models import DeliveryBusy, DeliveryUnknown, Readiness
from message_gateway.core.service import QueueFull, Service, Target
from tests.helpers import FakeAdapter


def run(coroutine: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coroutine)


@pytest.mark.parametrize("batching,expected", [(True, [4]), (False, [1, 1, 1, 1])])
def test_busy_then_ready_batches_in_order(batching: bool, expected: list[int]) -> None:
    async def scenario() -> None:
        adapter = FakeAdapter(Readiness.BUSY)
        service = Service({"work": Target(adapter, batching=batching)})
        for number in range(4):
            service.submit("work", "feed", str(number))
        await service.tick()
        assert adapter.batches == []
        adapter.state = Readiness.UNKNOWN
        await service.tick()
        assert adapter.batches == []
        adapter.state = Readiness.READY
        for _ in range(5):
            await service.tick()
        assert [len(batch) for batch in adapter.batches] == expected
        assert [m.content for b in adapter.batches for m in b] == ["0", "1", "2", "3"]
        assert service.targets["work"].last_result is not None
        assert service.status()["work"] is not None

    run(scenario())


def test_arrivals_during_send_and_duplicate_wakeup() -> None:
    async def scenario() -> None:
        adapter = FakeAdapter()
        adapter.release.clear()
        service = Service({"work": Target(adapter)})
        first = service.submit("work", "feed", "first")
        task = asyncio.create_task(service.tick())
        await adapter.entered.wait()
        second = service.submit("work", "feed", "second")
        await service.tick()
        assert service.targets["work"].sending == 1
        adapter.release.set()
        await task
        assert adapter.batches == [[first]]
        await service.tick()
        assert adapter.batches == [[first], [second]]
        await service.tick()
        assert len(adapter.batches) == 2

    run(scenario())


def test_batch_limit_and_busy_refusal_preserve_order() -> None:
    async def scenario() -> None:
        adapter = FakeAdapter()
        adapter.error = DeliveryBusy()
        service = Service({"work": Target(adapter)}, batch_limit=2)
        for value in ["a", "b", "c"]:
            service.submit("work", "feed", value)
        await service.tick()
        assert [m.content for m in service.targets["work"].queue] == ["a", "b", "c"]
        adapter.error = None
        await service.tick()
        await service.tick()
        assert [[m.content for m in b] for b in adapter.batches] == [["a", "b"], ["c"]]

    run(scenario())


def test_independent_targets_and_unknown_result_no_retry() -> None:
    async def scenario() -> None:
        broken, healthy = FakeAdapter(), FakeAdapter()
        broken.error = DeliveryUnknown("Connection lost")
        service = Service({"bad": Target(broken), "good": Target(healthy)})
        failed = service.submit("bad", "feed", "bad")
        service.submit("good", "feed", "good")
        await service.tick()
        assert len(healthy.batches) == 1
        assert service.targets["bad"].last_result == {
            "status": "uncertain",
            "messages": [failed.id],
            "error": "Connection lost",
        }
        broken.error = None
        await service.tick()
        assert broken.batches == []
        broken.read_error = RuntimeError("offline")
        await service.tick()
        assert service.targets["bad"].state == Readiness.UNKNOWN

    run(scenario())


def test_input_limits_and_fresh_process_state() -> None:
    adapter = FakeAdapter()
    service = Service({"work": Target(adapter)}, queue_limit=1)
    for channel, text in [
        ("", "x"),
        ("c" * 129, "x"),
        ("\ud800", "x"),
        ("c", " "),
        ("c", "x" * 65537),
    ]:
        with pytest.raises(ValueError):
            service.submit("work", channel, text)
    with pytest.raises(KeyError):
        service.submit("missing", "c", "x")
    original = "\u041f\u0440\u0438\u0432\u0435\u0442 \u4e16\u754c \U0001f600\r\n"
    assert service.submit("work", "feed", original).content == original
    with pytest.raises(QueueFull):
        service.submit("work", "feed", "extra")
    assert not Service({"work": Target(adapter)}).targets["work"].queue
    for targets, queue_limit, batch_limit in [
        ({}, 1, 1),
        ({"w": Target(adapter)}, 0, 1),
        ({"w": Target(adapter)}, 1, 0),
    ]:
        with pytest.raises(ValueError):
            Service(targets, queue_limit=queue_limit, batch_limit=batch_limit)


def test_run_wakes_polls_and_closes_on_cancel() -> None:
    async def scenario() -> None:
        adapter = FakeAdapter(Readiness.BUSY)
        service = Service({"work": Target(adapter)})
        with pytest.raises(ValueError):
            await service.run(0)
        task = asyncio.create_task(service.run(0.001))
        service.submit("work", "feed", "x")
        await asyncio.sleep(0.005)
        adapter.state = Readiness.READY
        async with asyncio.timeout(1):
            while not adapter.batches:
                await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert adapter.closed

    run(scenario())


def test_cancellation_during_send() -> None:
    async def scenario() -> None:
        adapter = FakeAdapter()
        adapter.release.clear()
        service = Service({"work": Target(adapter)}, queue_limit=1)
        service.submit("work", "feed", "x")
        task = asyncio.create_task(service.tick())
        await adapter.entered.wait()
        with pytest.raises(QueueFull):
            service.submit("work", "feed", "y")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.targets["work"].sending == 0
        assert service.targets["work"].last_result is not None

    run(scenario())


def test_diagnostics_explain_wait_and_delivery_without_poll_spam(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="message_gateway.diagnostics")

    async def scenario() -> None:
        adapter = FakeAdapter(Readiness.BUSY)
        service = Service({"work": Target(adapter)})
        message = service.submit(
            "work", "feed", "SECRET_BODY", sender="SECRET_SENDER", preview="SECRET_PREVIEW"
        )
        for _ in range(3):
            await service.tick()
        adapter.read_error = RuntimeError("SECRET_ERROR")
        for _ in range(3):
            await service.tick()
        adapter.read_error = None
        adapter.state = Readiness.READY
        await service.tick()
        await service.tick()
        events = [
            json.loads(r.message) for r in caplog.records if r.name == "message_gateway.diagnostics"
        ]
        assert "SECRET" not in caplog.text
        states = [e for e in events if e["event"] == "destination_state"]
        assert [(e["state"], e["reason"]) for e in states] == [
            ("busy", "busy"),
            ("unknown", "readiness_error"),
            ("ready", "ready"),
            ("ready", "empty"),
        ]
        assert states[0]["queued"] == 1 and states[0]["oldest_received_at"] == message.received_at
        assert states[1]["error_type"] == "RuntimeError"
        delivery = [e for e in events if e["event"].startswith("delivery_")]
        assert [e["event"] for e in delivery] == ["delivery_start", "delivery_accepted"]
        assert all(e["target"] == "work" and e["messages"] == [message.id] for e in delivery)
        assert delivery[1]["receipt"] == "receipt"

    run(scenario())


@pytest.mark.parametrize(
    "error,event",
    [
        (DeliveryBusy("SECRET_ERROR"), "delivery_deferred"),
        (DeliveryUnknown("SECRET_ERROR"), "delivery_uncertain"),
    ],
)
def test_delivery_failure_diagnostics(
    caplog: pytest.LogCaptureFixture, error: Exception, event: str
) -> None:
    caplog.set_level("INFO", logger="message_gateway.diagnostics")

    async def scenario() -> None:
        adapter = FakeAdapter()
        adapter.error = error
        service = Service({"work": Target(adapter)})
        message = service.submit("work", "feed", "SECRET_BODY")
        await service.tick()
        records = [
            json.loads(r.message) for r in caplog.records if r.name == "message_gateway.diagnostics"
        ]
        outcome = next(r for r in records if r["event"] == event)
        assert outcome["target"] == "work" and outcome["messages"] == [message.id]
        assert len(service.targets["work"].queue) == (1 if isinstance(error, DeliveryBusy) else 0)
        assert "SECRET" not in caplog.text
        if isinstance(error, DeliveryUnknown):
            assert outcome["error_type"] == "DeliveryUnknown"

    run(scenario())


def test_wait_diagnostics_update_queue_metrics_without_state_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="message_gateway.diagnostics")

    async def scenario() -> None:
        service = Service({"work": Target(FakeAdapter(Readiness.BUSY))})
        first = service.submit("work", "source", "first")
        await service.tick()
        await service.tick()
        service.submit("work", "source", "second")
        await service.tick()
        states = [
            json.loads(record.message)
            for record in caplog.records
            if '"event": "destination_state"' in record.message
        ]
        assert [state["queued"] for state in states] == [1, 2]
        assert all(state["oldest_received_at"] == first.received_at for state in states)

    run(scenario())
