import asyncio

from message_gateway.core.service import Service, Target
from message_gateway.interfaces.server import create_app
from tests.helpers import FakeAdapter, http_client


def test_http_validation_auth_and_shared_queue() -> None:
    async def scenario() -> None:
        service = Service({"work": Target(FakeAdapter())}, queue_limit=1)
        async with http_client(create_app(service, "secret")) as client:
            assert (await client.get("/v1/targets")).status == 401
            headers = {"Authorization": "Bearer secret"}
            assert (await client.get("/v1/targets", headers=headers)).status == 200
            path = "/v1/targets/work/messages"
            for body in [
                [],
                {},
                {"channel": 1, "content": "x"},
                {"channel": "c", "content": ""},
                {"channel": "c", "content": "x", "sender": 1},
                {"channel": "c", "content": "x", "preview": []},
                {"channel": "c", "content": "x", "sent_at": 42},
            ]:
                assert (await client.post(path, json=body, headers=headers)).status == 400
            assert (await client.post(path, data="{", headers=headers)).status == 400
            assert (
                await client.post("/v1/targets/absent/messages", json={}, headers=headers)
            ).status == 404
            response = await client.post(
                path, json={"channel": "c", "content": "x"}, headers=headers
            )
            assert response.status == 202
            assert (await response.json())["id"] == service.targets["work"].queue[0].id
            assert (
                await client.post(path, json={"channel": "c", "content": "y"}, headers=headers)
            ).status == 429
        async with http_client(create_app(service)) as client:
            assert (await client.get("/v1/targets")).status == 200

    asyncio.run(scenario())
