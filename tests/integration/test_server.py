import asyncio
from typing import Any

import aiohttp
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from message_gateway.core.models import Readiness
from message_gateway.core.service import Service, Target
from message_gateway.interfaces.server import create_app
from tests.helpers import FakeAdapter, running


def test_security_applies_to_rest_and_mcp() -> None:
    async def scenario() -> None:
        service = Service({"work": Target(FakeAdapter())})
        async with running(create_app(service, "secret")) as url:
            async with aiohttp.ClientSession() as client:
                for path in ("/mcp", "/v1/targets"):
                    assert (await client.get(url + path)).status == 401
                    assert (
                        await client.get(url + path, headers={"Authorization": "Bearer wrong"})
                    ).status == 401
                    headers = {"Authorization": "Bearer secret"}
                    assert (
                        await client.get(
                            url + path, headers={**headers, "Origin": "https://evil.example"}
                        )
                    ).status == 403
                    assert (
                        await client.get(url + path, headers={**headers, "Host": "evil.example"})
                    ).status == 403
                    assert (
                        await client.get(url + path, headers={**headers, "Host": "[invalid"})
                    ).status == 403
                    assert (
                        await client.post(url + path, data=b"x" * (512 * 1024 + 1), headers=headers)
                    ).status == 413
        assert not service.targets["work"].queue

    asyncio.run(scenario())


def test_multiple_clients_disconnect_without_owning_queues() -> None:
    async def scenario() -> None:
        adapter = FakeAdapter(Readiness.BUSY)
        service = Service({"work": Target(adapter)})
        async with running(create_app(service)) as url:

            async def send(value: str) -> dict[str, Any]:
                async with streamable_http_client(url + "/mcp") as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "send_message",
                            {"target": "work", "channel": "events", "content": value},
                        )
                        assert not result.is_error
                        assert isinstance(result.structured_content, dict)
                        return result.structured_content

            results = await asyncio.gather(send("one"), send("two"))
            assert {m.id for m in service.targets["work"].queue} == {r["id"] for r in results}
            assert not adapter.closed
            queued = list(service.targets["work"].queue)
            adapter.state = Readiness.READY
            await service.tick()
            assert adapter.batches[0] == queued

    asyncio.run(scenario())
