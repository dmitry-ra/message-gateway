import asyncio
import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from message_gateway import __version__
from message_gateway.core.models import JsonValue
from message_gateway.core.service import Service, Target
from message_gateway.interfaces.models import Submission
from message_gateway.interfaces.server import create_app
from tests.helpers import FakeAdapter, http_client


def test_specification_matches_real_responses() -> None:
    spec = json.loads(files("message_gateway.interfaces").joinpath("openapi.json").read_text())
    assert spec["info"]["version"] == __version__
    assert set(spec["components"]["schemas"]["Submission"]["properties"]) == set(
        Submission.model_fields
    )

    def validate(name: str, value: Any) -> None:
        Draft202012Validator(spec, format_checker=FormatChecker()).evolve(
            schema={"$ref": "#/components/schemas/" + name}
        ).validate(value)

    async def scenario() -> None:
        adapter = FakeAdapter()
        service = Service({"work": Target(adapter)})
        values: list[JsonValue] = [
            "\u041f\u0440\u0438\u0432\u0435\u0442\r\n",
            {"items": [1, True, None, {"x": "\U0001f600"}]},
            ["x", 3],
            42,
            False,
            None,
        ]
        async with http_client(create_app(service)) as client:
            assert await (await client.get("/openapi.json")).json() == spec
            validate("Targets", await (await client.get("/v1/targets")).json())
            for value in values:
                body = {"channel": "builds", "content": value}
                validate("Submission", body)
                response = await client.post("/v1/targets/work/messages", json=body)
                assert response.status == 202
                result = await response.json()
                validate("Message", result)
                assert result["content"] == value
            for invalid_body in [
                {"channel": "c", "content": float("nan")},
                {"channel": "c", "content": "\ud800"},
            ]:
                response = await client.post("/v1/targets/work/messages", json=invalid_body)
                assert response.status == 400
                validate("Error", await response.json())
            await service.tick()
            assert [message.content for message in adapter.batches[0]] == values
            validate("Targets", await (await client.get("/v1/targets")).json())
        payload: JsonValue = {"a": [1]}
        message = service.submit("work", "c", payload)
        assert isinstance(payload, dict)
        payload["a"] = []
        assert message.content == {"a": [1]}

    asyncio.run(scenario())


def test_timestamp_schema_enforces_date_time() -> None:
    spec = json.loads(files("message_gateway.interfaces").joinpath("openapi.json").read_text())
    checker = FormatChecker(formats=["date-time"])
    assert not checker.conforms("2026-11-04X12:00:00+00:00", "date-time")
    validator = Draft202012Validator(spec, format_checker=checker).evolve(
        schema={"$ref": "#/components/schemas/SentAt"}
    )
    for value in [
        "2026-11-04T12:00:00Z",
        "2026-11-04t12:00:00z",
        "2026-11-04T12:00:00.123456789+03:30",
        "2026-11-04T12:00:00-05:00",
    ]:
        validator.validate(value)
    for value in [
        "2026-11-04X12:00:00+00:00",
        "2026-11-04 12:00:00+00:00",
        "2026-11-04T12:00Z",
        "2026-11-04T12:00:00+0300",
    ]:
        assert not validator.is_valid(value), value
