"""Opt-in acceptance against a dedicated interactive Codex session."""

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from message_gateway.adapters.codex import CodexAdapter, format_messages
from message_gateway.core.models import JsonValue, Message, Readiness
from message_gateway.core.service import Service, Target
from message_gateway.interfaces.server import create_app
from tests.helpers import running

pytestmark = pytest.mark.live


class RecordingAdapter(CodexAdapter):
    def __init__(self, thread: str, socket: Path) -> None:
        super().__init__(thread, socket)
        self.receipts: list[str] = []
        self.delivered: list[list[Message]] = []

    async def send(self, messages: list[Message]) -> str:
        receipt = await super().send(messages)
        self.receipts.append(receipt)
        self.delivered.append(messages)
        return receipt


@pytest.mark.parametrize("batching", [True, False])
def test_live_busy_delivery(batching: bool) -> None:
    thread = os.environ.get("MESSAGE_GATEWAY_TEST_THREAD")
    if not thread:
        pytest.skip("Set MESSAGE_GATEWAY_TEST_THREAD to a dedicated test session")
    socket = Path(
        os.environ.get(
            "MESSAGE_GATEWAY_TEST_SOCKET", "~/.codex/app-server-control/app-server-control.sock"
        )
    ).expanduser()

    async def scenario() -> None:
        adapter = RecordingAdapter(thread, socket)
        controller = CodexAdapter(thread, socket)
        worker: asyncio.Task[None] | None = None
        try:
            async with asyncio.timeout(120):
                while await controller.readiness() != Readiness.READY:
                    await asyncio.sleep(0.1)
                await controller._request(
                    "thread/resume", {"threadId": thread, "excludeTurns": True}
                )
                await controller._request(
                    "turn/start",
                    {
                        "threadId": thread,
                        "effort": "low",
                        "input": [
                            {
                                "type": "text",
                                "text": 'Use a shell tool to run python3 -c "import time; time.sleep(30)". Wait until it finishes, then reply exactly LONG_DONE. Do not change files.',
                            }
                        ],
                    },
                )
                while await controller.readiness() != Readiness.BUSY:
                    await asyncio.sleep(0.1)
                service = Service({"work": Target(adapter, batching=batching)})
                worker = asyncio.create_task(service.run(0.1))
                padding = "This is a deliberately long preview-only introduction. " * 3
                values: list[JsonValue] = [
                    padding + "Reply with the token WILLOW.\r\n",
                    {
                        "description": padding,
                        "request": "Reply with the token COPPER.",
                        "unicode": "\u041f\u0440\u0438\u0432\u0435\u0442 \u4e16\u754c \U0001f600",
                    },
                    padding + "Reply with the token VIOLET.",
                    {
                        "description": padding,
                        "request": "Reply with the token MARBLE.",
                        "items": [1, True, None],
                    },
                ]
                async with running(create_app(service)) as url:
                    async with streamable_http_client(url + "/mcp") as (read, write):
                        async with ClientSession(read, write) as client:
                            await client.initialize()
                            for value in values:
                                response = await client.call_tool(
                                    "send_message",
                                    {
                                        "target": "work",
                                        "channel": "Live test",
                                        "content": value,
                                    },
                                )
                                assert not response.is_error
                    assert len(service.targets["work"].queue) == 4
                    assert adapter.receipts == []
                    expected = 1 if batching else 4
                    while len(adapter.receipts) < expected:
                        await asyncio.sleep(0.1)
                    while await controller.readiness() != Readiness.READY:
                        await asyncio.sleep(0.1)
                assert [len(batch) for batch in adapter.delivered] == (
                    [4] if batching else [1, 1, 1, 1]
                )
                assert [m.content for b in adapter.delivered for m in b] == values
                visible = format_messages(thread, [m for b in adapter.delivered for m in b])[
                    "input"
                ][0]["text"]
                for token in ["WILLOW", "COPPER", "VIOLET", "MARBLE"]:
                    assert token not in visible
                result = await controller._request(
                    "thread/read", {"threadId": thread, "includeTurns": True}
                )
                turns = [
                    turn for turn in result["thread"]["turns"] if turn["id"] in adapter.receipts
                ]
                answers = " ".join(
                    item.get("text", "")
                    for turn in turns
                    for item in turn["items"]
                    if item.get("type") == "agentMessage"
                )
                for token in ["WILLOW", "COPPER", "VIOLET", "MARBLE"]:
                    assert token in answers, answers
                print(
                    json.dumps(
                        {"batching": batching, "turns": adapter.receipts, "answers": answers}
                    )
                )
        finally:
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            await adapter.close()
            await controller.close()

    asyncio.run(scenario())
