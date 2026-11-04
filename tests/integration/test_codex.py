import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from message_gateway.adapters.codex import CodexAdapter, format_messages
from message_gateway.core.models import DeliveryBusy, DeliveryUnknown, Message, Readiness


class AppServer:
    def __init__(self) -> None:
        self.state = "idle"
        self.identity = "thread"
        self.resume_identity = "thread"
        self.resume_state = "idle"
        self.turn_id: object = "turn"
        self.failure = ""
        self.requests: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for frame in ws:
            value = json.loads(frame.data)
            self.requests.append(value)
            for notification in self.notifications:
                await ws.send_json(notification)
            self.notifications.clear()
            if "id" not in value:
                continue
            method = value["method"]
            if method == self.failure:
                await ws.send_json({"id": value["id"], "error": {"message": "refused"}})
                continue
            if method == "turn/start" and self.failure == "disconnect":
                await ws.close()
                return ws
            if method == "turn/start" and self.failure == "timeout":
                await asyncio.sleep(0.05)
                return ws
            if method == "thread/read":
                result: object = {"thread": {"id": self.identity, "status": {"type": self.state}}}
            elif method == "thread/resume":
                result = {
                    "thread": {"id": self.resume_identity, "status": {"type": self.resume_state}}
                }
            elif method == "turn/start":
                result = {"turn": {"id": self.turn_id}}
            else:
                result = {}
            if self.failure == "invalid":
                result = []
            await ws.send_json({"method": "thread/status/changed", "params": {}})
            await ws.send_json({"id": value["id"], "result": result})
        return ws

    @asynccontextmanager
    async def connect(self, path: Path, timeout: float = 1) -> AsyncIterator[CodexAdapter]:
        app = web.Application()
        app.router.add_get("/", self.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.UnixSite(runner, str(path)).start()
        adapter = CodexAdapter("thread", path, timeout=timeout)
        try:
            yield adapter
        finally:
            await adapter.close()
            await runner.cleanup()


def test_codex_success_and_formatting(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = AppServer()
        async with server.connect(tmp_path / "server.sock") as adapter:
            adapter.effort = "low"
            messages = [
                Message.create(
                    "\u041a\u0430\u043d\u0430\u043b \U0001f600", "prefix " * 30 + "TAIL\r\n"
                ),
                Message.create("json\x1b\nfeed", {"x": ["\u4e16\u754c", True, None]}),
            ]
            assert await adapter.readiness() == Readiness.READY
            assert await adapter.send(messages) == "turn"
            params = [r for r in server.requests if r["method"] == "turn/start"][0]["params"]
            assert params["effort"] == "low"
            originals = json.loads(params["additionalContext"]["gateway_packet"]["value"])
            assert [m["content"] for m in originals["messages"]] == [m.content for m in messages]
            text = params["input"][0]["text"]
            assert "TAIL" not in text and "\x1b" not in text
            encoded = text.encode()
            for element in params["input"][0]["text_elements"]:
                region = element["byteRange"]
                assert encoded[region["start"] : region["end"]].decode() == element["placeholder"]
            server.state = "active"
            assert await adapter.readiness() == Readiness.BUSY
            with pytest.raises(DeliveryBusy):
                await adapter.send(messages)
            server.state = "notLoaded"
            assert await adapter.readiness() == Readiness.READY
        assert format_messages("thread", [])["threadId"] == "thread"

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["idle", "notLoaded", "systemError", "futureState"])
def test_codex_delivers_in_non_active_states(tmp_path: Path, state: str) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = state
        async with server.connect(tmp_path / "server.sock") as adapter:
            assert await adapter.readiness() == Readiness.READY
            assert await adapter.send([Message.create("c", {"event": 1})]) == "turn"
            assert len([r for r in server.requests if r["method"] == "turn/start"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["thread/read", "thread/resume"])
@pytest.mark.parametrize("state", [None, "", 42])
def test_codex_rejects_invalid_states(tmp_path: Path, method: str, state: Any) -> None:
    async def scenario() -> None:
        server = AppServer()
        if method == "thread/read":
            server.state = state
        else:
            server.resume_state = state
        async with server.connect(tmp_path / "server.sock") as adapter:
            with pytest.raises(DeliveryBusy):
                await adapter.send([Message.create("c", "x")])
            assert not any(r["method"] == "turn/start" for r in server.requests)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["disconnect", "timeout", "turn/start", "receipt", "resume_identity", "resume_busy"]
)
def test_codex_send_failures(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        server = AppServer()
        if failure == "receipt":
            server.turn_id = None
        elif failure == "resume_identity":
            server.resume_identity = "other"
        elif failure == "resume_busy":
            server.resume_state = "active"
        else:
            server.failure = failure
        async with server.connect(tmp_path / "server.sock", timeout=0.02) as adapter:
            expected = (
                DeliveryBusy if failure in ("resume_busy", "resume_identity") else DeliveryUnknown
            )
            with pytest.raises(expected):
                await adapter.send([Message.create("c", "x")])
            assert len([r for r in server.requests if r["method"] == "turn/start"]) <= 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["initialize", "thread/read", "invalid", "identity"])
def test_codex_read_failures(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        server = AppServer()
        if failure == "identity":
            server.identity = "other"
        else:
            server.failure = failure
        async with server.connect(tmp_path / "server.sock") as adapter:
            with pytest.raises(DeliveryUnknown):
                await adapter.readiness()
            assert adapter.client is None
        unconnected = CodexAdapter("thread", tmp_path / "missing")
        with pytest.raises(DeliveryUnknown):
            await unconnected._request("thread/read", {})
        with pytest.raises(OSError):
            await unconnected.readiness()
        assert unconnected.client is None

    asyncio.run(scenario())


def test_codex_diagnostics_filter_and_correlate(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level("INFO", logger="message_gateway.diagnostics")

    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "systemError"
        error = {"codexErrorInfo": "server_overloaded", "message": "SECRET_BODY"}
        server.notifications = [
            {"method": "error", "params": {"threadId": "other", "error": error}},
            {
                "method": "error",
                "params": {
                    "threadId": "thread",
                    "turnId": "failed",
                    "error": error,
                    "willRetry": False,
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {
                        "id": "failed",
                        "status": "failed",
                        "error": error,
                        "items": ["SECRET_BODY"],
                    },
                },
            },
            {
                "method": "error",
                "params": {
                    "threadId": "thread",
                    "turnId": "retry",
                    "error": {
                        "codexErrorInfo": {
                            "httpConnectionFailed": {"httpStatusCode": 503, "body": "SECRET_BODY"}
                        }
                    },
                    "willRetry": True,
                },
            },
            {
                "method": "thread/status/changed",
                "params": {"threadId": "thread", "status": {"type": "systemError"}},
            },
            {
                "method": "thread/status/changed",
                "params": {"threadId": "thread", "status": {"type": "systemError"}},
            },
            {
                "method": "thread/status/changed",
                "params": {
                    "threadId": "thread",
                    "status": {"type": "active", "activeFlags": ["waitingOnApproval"]},
                },
            },
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread", "delta": "SECRET_BODY"},
            },
            {"method": "error", "params": None},
            {
                "method": "turn/completed",
                "params": {"threadId": "thread", "turn": None, "error": None},
            },
        ]
        async with server.connect(tmp_path / "server.sock") as adapter:
            await adapter.readiness()
            await adapter.readiness()
            assert await adapter.send([Message.create("c", "SECRET_BODY")]) == "turn"
        events = [
            json.loads(r.message) for r in caplog.records if r.name == "message_gateway.diagnostics"
        ]
        assert "SECRET_BODY" not in caplog.text
        assert all(e["thread_id"] == "thread" for e in events)
        states = [e for e in events if e["event"] == "codex_state"]
        assert [(e["stage"], e["state"]) for e in states] == [
            ("notification", "systemError"),
            ("notification", "active"),
            ("read", "systemError"),
            ("resume", "systemError"),
        ]
        assert states[1]["active_flags"] == ["waitingOnApproval"]
        turns = [e for e in events if e["event"] == "codex_turn"]
        assert [(e["notification"], e["turn_id"]) for e in turns] == [
            ("error", "failed"),
            ("turn/completed", "failed"),
            ("error", "retry"),
            ("turn/completed", None),
        ]
        assert turns[0]["error_info"] == "serverOverloaded" and turns[0]["will_retry"] is False
        assert turns[1]["status"] == "failed"
        assert turns[2]["http_status"] == 503 and turns[2]["will_retry"] is True

    asyncio.run(scenario())
