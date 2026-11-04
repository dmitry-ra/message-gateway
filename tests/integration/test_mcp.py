import asyncio
import json
from typing import Any

import aiohttp
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult

from message_gateway.core.models import Readiness
from message_gateway.core.service import Service, Target
from message_gateway.interfaces.server import create_app
from tests.helpers import FakeAdapter, running


def test_real_network_mcp_shares_http_queue() -> None:
    async def scenario() -> None:
        adapter = FakeAdapter(Readiness.BUSY)
        service = Service({"work": Target(adapter)}, queue_limit=3)
        async with running(create_app(service, "secret")) as url:
            async with create_mcp_http_client(headers={"Authorization": "Bearer secret"}) as http:
                async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert {tool.name for tool in tools.tools} == {
                            "list_targets",
                            "send_message",
                        }
                        assert all(
                            tool.input_schema["additionalProperties"] is False
                            for tool in tools.tools
                        )
                        content = {
                            "build": 42,
                            "parts": ["\u041f\u0440\u0438\u0432\u0435\u0442", None],
                        }
                        result = await session.call_tool(
                            "send_message",
                            {
                                "target": "work",
                                "channel": "builds",
                                "content": content,
                                "sender": "name",
                                "preview": "Display only",
                                "sent_at": "2026-10-31T22:23:07Z",
                            },
                        )
                        assert isinstance(result, CallToolResult) and not result.is_error
                        message = service.targets["work"].queue[0]
                        assert message.content == content and message.sender == "name"
                        assert message.preview == "Display only"
                        assert message.sent_at == "2026-10-31T22:23:07Z"
                        response = await http.post(
                            url + "/v1/targets/work/messages",
                            json={"channel": "rest", "content": False},
                        )
                        assert response.status_code == 202
                        assert not (
                            await session.call_tool(
                                "send_message", {"target": "work", "channel": "c", "content": None}
                            )
                        ).is_error
                        listed = await session.call_tool("list_targets", {})
                        assert "work" in json.dumps(listed.model_dump())
                        full = await session.call_tool(
                            "send_message", {"target": "work", "channel": "c", "content": "full"}
                        )
                        assert full.is_error
                        invalid = await session.call_tool(
                            "send_message", {"target": "absent", "channel": "c", "content": "x"}
                        )
                        assert invalid.is_error
            # Disconnecting the client cannot discard queued work or stop the daemon.
            assert [m.content for m in service.targets["work"].queue] == [content, False, None]
            adapter.state = Readiness.READY
            await service.tick()
            assert [m.content for m in adapter.batches[0]] == [content, False, None]
            async with aiohttp.ClientSession() as http:
                assert (await http.get(url + "/v1/targets")).status == 401

    asyncio.run(scenario())


def test_mcp_rejects_bad_arguments_without_coercion() -> None:
    async def scenario() -> None:
        service = Service({"work": Target(FakeAdapter())})
        async with running(create_app(service)) as url:
            async with streamable_http_client(url + "/mcp") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    base = {"target": "work", "channel": "c", "content": "x"}
                    invalid_arguments: list[dict[str, Any]] = [
                        {"target": 123},
                        {"channel": 123},
                        {"channel": True},
                        {"sender": 42},
                        {"preview": []},
                        {"sent_at": 42},
                        {"extra": "unknown"},
                        {"channel": " "},
                        {"content": " "},
                        {"content": "x" * 65537},
                        {"sent_at": "2026-11-01T12:00:00"},
                    ]
                    for changes in invalid_arguments:
                        result = await session.call_tool("send_message", {**base, **changes})
                        assert result.is_error, changes
                    assert (await session.call_tool("list_targets", {"extra": 1})).is_error
                    assert not service.targets["work"].queue

    asyncio.run(scenario())


def test_http_and_mcp_share_timestamp_validation() -> None:
    async def scenario() -> None:
        service = Service({"work": Target(FakeAdapter())})
        async with running(create_app(service)) as url:
            async with create_mcp_http_client() as http:
                async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        for sent_at, accepted in [
                            ("2026-11-04X12:00:00+00:00", False),
                            ("2026-11-04 12:00:00+00:00", False),
                            ("2026-11-04T12:00Z", False),
                            ("2026-11-04T12:00:00+0300", False),
                            ("2026-11-04T12:00:00", False),
                            ("2026-02-30T12:00:00Z", False),
                            ("2026-11-04T12:00:00Z", True),
                            ("2026-11-04t12:00:00z", True),
                            ("2026-11-04T12:00:00.123456789+03:30", True),
                            ("2026-11-04T12:00:00-05:00", True),
                        ]:
                            before = len(service.targets["work"].queue)
                            body = {"channel": "source", "content": "x", "sent_at": sent_at}
                            response = await http.post(url + "/v1/targets/work/messages", json=body)
                            assert response.status_code == (202 if accepted else 400), sent_at
                            result = await session.call_tool(
                                "send_message", {"target": "work", **body}
                            )
                            assert result.is_error is not accepted, sent_at
                            queued = list(service.targets["work"].queue)[before:]
                            assert [message.sent_at for message in queued] == (
                                [sent_at, sent_at] if accepted else []
                            )
                            if accepted:
                                assert response.json()["sent_at"] == sent_at
                                assert result.structured_content is not None
                                assert result.structured_content["sent_at"] == sent_at

    asyncio.run(scenario())
