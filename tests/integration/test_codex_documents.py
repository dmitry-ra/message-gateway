import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from message_gateway.adapters import codex
from message_gateway.adapters.codex import CodexAdapter, packet_context
from message_gateway.core.models import DeliveryBusy, DeliveryUnknown, Message, Readiness
from message_gateway.core.service import Service, Target
from tests.integration.test_codex import AppServer


def test_documents_survive_reconnect_and_active_state_until_verified_idle(tmp_path: Path) -> None:
    async def run() -> None:
        server = AppServer()
        async with server.connect(tmp_path / "server.sock") as adapter:
            message = Message.create("source", "x" * 8000)
            await adapter.send([message])
            path = Path(adapter.documents.name) / (message.id + ".json")
            assert path.exists()
            await adapter.close()
            assert path.exists()
            server.state = "active"
            assert await adapter.readiness() == Readiness.BUSY
            assert path.exists()
            server.state = "idle"
            server.identity = "other"
            with pytest.raises(DeliveryUnknown):
                await adapter.readiness()
            assert path.exists()
            server.identity = "thread"
            assert await adapter.readiness() == Readiness.READY
            assert not path.exists()

    asyncio.run(run())


@pytest.mark.parametrize("limit", ["CONTEXT_DOCUMENT_BYTES", "CONTEXT_DOCUMENT_FILES"])
def test_document_quota_preserves_existing_files_without_partial_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    first = Message.create("source", "a" * 8000)
    packet_context([first], tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(
        codex, limit, len(next(iter(before.values()))) if limit.endswith("BYTES") else 1
    )
    with pytest.raises(DeliveryBusy):
        packet_context([Message.create("source", "b" * 8000)], tmp_path)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_document_quota_defers_unsent_fifo_then_recovers(tmp_path: Path) -> None:
    async def run() -> None:
        server = AppServer()
        async with server.connect(tmp_path / "server.sock") as adapter:
            service = Service({"target": Target(adapter)})
            message = service.submit("target", "source", "a" * 8000)
            with patch.object(codex, "CONTEXT_DOCUMENT_BYTES", 1):
                await service.tick()
            assert list(service.targets["target"].queue) == [message]
            assert not list(Path(adapter.documents.name).iterdir())
            assert not any(r["method"] == "turn/start" for r in server.requests)
            await service.tick()
            assert not service.targets["target"].queue
            assert len([r for r in server.requests if r["method"] == "turn/start"]) == 1

    asyncio.run(run())


def test_unknown_state_before_submission_preserves_fifo(tmp_path: Path) -> None:
    async def run() -> None:
        server = AppServer()
        async with server.connect(tmp_path / "server.sock") as adapter:
            service = Service({"target": Target(adapter)})
            message = service.submit("target", "source", "original")
            with patch.object(
                adapter, "readiness", AsyncMock(side_effect=[Readiness.READY, Readiness.UNKNOWN])
            ):
                await service.tick()
            assert list(service.targets["target"].queue) == [message]
            assert not server.requests
            await service.tick()
            assert not service.targets["target"].queue
            assert len([r for r in server.requests if r["method"] == "turn/start"]) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "thread",
    [
        None,
        {},
        {"id": "thread"},
        {"id": "thread", "status": []},
        {"id": "thread", "status": {"type": ""}},
        {"id": "thread", "status": {"type": 1}},
    ],
)
def test_invalid_thread_envelopes_are_normalized(tmp_path: Path, thread: object) -> None:
    adapter = CodexAdapter("thread", tmp_path / "unused")
    with pytest.raises(DeliveryUnknown):
        adapter._thread({"thread": thread})
