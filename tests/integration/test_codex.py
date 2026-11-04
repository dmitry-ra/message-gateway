import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from message_gateway.adapters.codex import CodexAdapter, format_messages
from message_gateway.core.models import DeliveryBusy, DeliveryUnknown, JsonValue, Message, Readiness
from message_gateway.core.service import Service, Target


class AppServer:
    def __init__(self) -> None:
        self.state = "idle"
        self.identity = "thread"
        self.resume_identity = "thread"
        self.resume_state = "idle"
        self.turn_id: object = "turn"
        self.turns: object = [{"id": "active-turn", "status": "inProgress"}]
        self.steer_receipt: object = "active-turn"
        self.steer_error: object = None
        self.failure = ""
        self.prepare_method = ""
        self.prepare_failure = ""
        self.prepare_after = 1
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
            if (
                method == self.prepare_method
                and len([r for r in self.requests if r.get("method") == method])
                >= self.prepare_after
            ):
                if self.prepare_failure == "disconnect":
                    await ws.close()
                    return ws
                if self.prepare_failure == "timeout":
                    await asyncio.sleep(0.05)
                    return ws
                response: dict[str, Any] = (
                    {"error": {"message": "preflight failure"}}
                    if self.prepare_failure == "error"
                    else {"result": []}
                )
                await ws.send_json({"id": value["id"], **response})
                continue
            if method == "turn/steer" and self.steer_error is not None:
                await ws.send_json({"id": value["id"], "error": self.steer_error})
                continue
            if method == self.failure:
                await ws.send_json({"id": value["id"], "error": {"message": "refused"}})
                continue
            if method in ("turn/start", "turn/steer") and self.failure == "disconnect":
                await ws.close()
                return ws
            if method in ("turn/start", "turn/steer") and self.failure == "timeout":
                await asyncio.sleep(0.05)
                return ws
            if method == "thread/read":
                result: object = {"thread": {"id": self.identity, "status": {"type": self.state}}}
            elif method == "thread/resume":
                result = {
                    "thread": {"id": self.resume_identity, "status": {"type": self.resume_state}}
                }
            elif method == "thread/turns/list":
                result = {"data": self.turns}
            elif method == "turn/start":
                result = {"turn": {"id": self.turn_id}}
            elif method == "turn/steer":
                result = {"turnId": self.steer_receipt}
            else:
                result = {}
            if self.failure == "invalid":
                result = []
            await ws.send_json({"method": "thread/status/changed", "params": {}})
            await ws.send_json({"id": value["id"], "result": result})
        return ws

    @asynccontextmanager
    async def connect(
        self, path: Path, timeout: float = 1, *, interactive: bool = False
    ) -> AsyncIterator[CodexAdapter]:
        app = web.Application()
        app.router.add_get("/", self.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.UnixSite(runner, str(path)).start()
        adapter = CodexAdapter("thread", path, timeout=timeout, interactive=interactive)
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
            with pytest.raises(DeliveryUnknown):
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
            expected = DeliveryBusy if failure == "resume_busy" else DeliveryUnknown
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
        assert turns[0]["error_info"] == "server_overloaded" and turns[0]["will_retry"] is False
        assert turns[1]["status"] == "failed"
        assert turns[2]["http_status"] == 503 and turns[2]["will_retry"] is True

    asyncio.run(scenario())


def test_interactive_delivery_preserves_originals_and_active_turn(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level("INFO", logger="message_gateway.diagnostics")

    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            adapter.effort = "high"
            assert await adapter.readiness() == Readiness.READY
            original: JsonValue = {
                "text": "PRIVATE_BODY",
                "quote": {"text": "selected quote"},
                "reply_to_message": {"message_id": 42},
                "downloads": [{"path": "/synthetic/photo.png", "status": "saved"}],
            }
            service = Service({"chat": Target(adapter, batching=False)})
            message = service.submit("chat", "opaque", original, preview="short")
            service.submit("chat", "opaque", "second")
            await service.tick()
            assert len(service.targets["chat"].queue) == 1
            requests = [r for r in server.requests if r["method"] == "turn/steer"]
            assert len(requests) == 1
            params = requests[0]["params"]
            assert params["expectedTurnId"] == "active-turn"
            lookup = next(r for r in server.requests if r["method"] == "thread/turns/list")
            assert lookup["params"] == {
                "threadId": "thread",
                "limit": 1,
                "sortDirection": "desc",
                "itemsView": "notLoaded",
            }
            assert all(
                not r["params"]["includeTurns"]
                for r in server.requests
                if r["method"] == "thread/read"
            )
            assert "effort" not in params
            packet = json.loads(params["additionalContext"]["gateway_packet"]["value"])
            assert packet["messages"][0]["id"] == message.id
            assert packet["messages"][0]["content"] == original
            assert "PRIVATE_BODY" not in params["input"][0]["text"]
            assert not any(r["method"] == "turn/start" for r in server.requests)
            assert service.targets["chat"].last_result == {
                "status": "accepted",
                "messages": [message.id],
                "receipt": "active-turn",
            }
        events = [json.loads(r.message) for r in caplog.records if r.name.endswith("diagnostics")]
        event = next(e for e in events if e["event"] == "codex_delivery")
        assert event["method"] == "turn/steer" and event["turn_id"] == "active-turn"
        assert "PRIVATE_BODY" not in caplog.text
        assert all(e["readiness"] == "ready" for e in events if e["event"] == "codex_state")

    asyncio.run(scenario())


@pytest.mark.parametrize("read_state,resume_state", [("active", "idle"), ("idle", "active")])
def test_interactive_rechecks_state_before_delivery(
    tmp_path: Path, read_state: str, resume_state: str
) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state, server.resume_state = read_state, resume_state
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            receipt = await adapter.send([Message.create("c", "message")])
            assert receipt == ("active-turn" if resume_state == "active" else "turn")

    asyncio.run(scenario())


def test_interactive_turn_completes_during_identity_lookup(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        server.turns = [{"id": "active-turn", "status": "completed"}]
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            with pytest.raises(DeliveryBusy):
                await adapter.send([Message.create("c", "message")])
            assert not any(r["method"].startswith("turn/") for r in server.requests)
            server.state = server.resume_state = "idle"
            adapter.effort = "low"
            assert await adapter.send([Message.create("c", "message")]) == "turn"
            start = next(r for r in server.requests if r["method"] == "turn/start")
            assert start["params"]["effort"] == "low"
            assert "expectedTurnId" not in start["params"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "message",
    [
        "no active turn to steer",
        "expected active turn id `active-turn` but found `new-turn`",
        "cannot steer a review turn",
        "cannot steer a compact turn",
    ],
)
def test_explicit_steer_refusal_retains_queue(tmp_path: Path, message: str) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        server.steer_error = {"code": -32600, "message": message}
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            service = Service({"chat": Target(adapter)})
            queued = service.submit("chat", "c", "original")
            await service.tick()
            assert list(service.targets["chat"].queue) == [queued]
            assert service.targets["chat"].last_result is None
            assert not any(r["method"] == "turn/start" for r in server.requests)
            server.state = server.resume_state = "idle"
            server.steer_error = None
            await service.tick()
            assert not service.targets["chat"].queue
            result = service.targets["chat"].last_result
            assert result is not None and result["status"] == "accepted"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error",
    [
        {"code": -32603, "message": "no active turn to steer"},
        {"code": -32600, "message": "unrecognized native failure"},
        {"code": -32600, "message": "expected active turn id `other` but found `new-turn`"},
        {"code": -32600, "message": None},
        "invalid error envelope",
    ],
)
def test_other_steer_errors_are_uncertain(tmp_path: Path, error: object) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        server.steer_error = error
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            with pytest.raises(DeliveryUnknown):
                await adapter.send([Message.create("c", "original")])
            assert adapter.client is None
            assert len([r for r in server.requests if r["method"] == "turn/steer"]) == 1
            assert not any(r["method"] == "turn/start" for r in server.requests)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["disconnect", "timeout", "receipt", "wrong_receipt"])
def test_uncertain_steer_is_not_replayed(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        if failure in ("receipt", "wrong_receipt"):
            server.steer_receipt = None if failure == "receipt" else "other-turn"
        else:
            server.failure = failure
        async with server.connect(
            tmp_path / "server.sock", timeout=0.02, interactive=True
        ) as adapter:
            service = Service({"chat": Target(adapter)})
            service.submit("chat", "c", "original")
            await service.tick()
            result = service.targets["chat"].last_result
            assert result is not None and result["status"] == "uncertain"
            assert not service.targets["chat"].queue
            await service.tick()
            assert len([r for r in server.requests if r["method"] == "turn/steer"]) == 1
            assert not any(r["method"] == "turn/start" for r in server.requests)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "turns",
    [
        [],
        [{"id": "old", "status": "completed"}],
        [{"id": "one", "status": "inProgress"}, {"id": "two", "status": "inProgress"}],
    ],
)
def test_interactive_does_not_guess_active_turn(tmp_path: Path, turns: object) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        server.turns = turns
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            with pytest.raises(DeliveryBusy):
                await adapter.send([Message.create("c", "message")])
            assert not any(r["method"].startswith("turn/") for r in server.requests)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["turns", "turn_id", "lookup"])
def test_interactive_rejects_invalid_active_identity(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        if failure == "turns":
            server.turns = None
        elif failure == "turn_id":
            server.turns = [{"id": "", "status": "inProgress"}]
        else:
            server.failure = "thread/turns/list"
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            with pytest.raises(DeliveryBusy):
                await adapter.send([Message.create("c", "message")])
            assert not any(r["method"].startswith("turn/") for r in server.requests)

    asyncio.run(scenario())


def test_interactive_large_context_uses_complete_documents(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        async with server.connect(tmp_path / "server.sock", interactive=True) as adapter:
            original: JsonValue = {"body": "x" * 8000 + "END"}
            assert (
                await adapter.send([Message.create("c", original, preview="short")])
                == "active-turn"
            )
            steer = next(r for r in server.requests if r["method"] == "turn/steer")
            context = steer["params"]["additionalContext"]
            manifest = json.loads(context["gateway_packet"]["value"])
            assert manifest["format"] == "message_items"
            document = json.loads(context[manifest["items"][0]]["value"])
            assert json.loads(Path(document["content_file"]).read_text())["content"] == original

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["thread/read", "thread/resume", "thread/turns/list"])
@pytest.mark.parametrize("failure", ["error", "invalid", "timeout", "disconnect"])
def test_interactive_preflight_failures_preserve_fifo_until_recovery(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, method: str, failure: str
) -> None:
    caplog.set_level("INFO", logger="message_gateway.diagnostics")

    async def scenario() -> None:
        server = AppServer()
        server.state = server.resume_state = "active"
        server.prepare_method = method
        server.prepare_failure = failure
        # Service checks readiness before dequeuing; exercise the send-side read.
        server.prepare_after = 2 if method == "thread/read" else 1
        async with server.connect(
            tmp_path / "server.sock", timeout=0.02, interactive=True
        ) as adapter:
            service = Service({"chat": Target(adapter)})
            first = service.submit("chat", "c", "first")
            second = service.submit("chat", "c", "second")
            await service.tick()
            assert list(service.targets["chat"].queue) == [first, second]
            assert service.targets["chat"].last_result is None
            assert adapter.client is None
            assert not any(r["method"].startswith("turn/") for r in server.requests)
            events = [
                json.loads(r.message)
                for r in caplog.records
                if r.name == "message_gateway.diagnostics"
            ]
            event = next(e for e in events if e["event"] == "codex_delivery_deferred")
            assert event["reason"] == "preflight_error"
            assert (
                event["stage"]
                == {
                    "thread/read": "readiness",
                    "thread/resume": "resume",
                    "thread/turns/list": "turn_lookup",
                }[method]
            )
            assert isinstance(event["error_type"], str)
            server.prepare_method = ""
            await service.tick()
            assert not service.targets["chat"].queue
            result = service.targets["chat"].last_result
            assert result is not None and result["status"] == "accepted"
            assert result["messages"] == [first.id, second.id]
            await service.tick()
            requests = [r for r in server.requests if r["method"] == "turn/steer"]
            assert len(requests) == 1
            packet = json.loads(
                requests[0]["params"]["additionalContext"]["gateway_packet"]["value"]
            )
            assert [message["id"] for message in packet["messages"]] == [first.id, second.id]

    asyncio.run(scenario())
