import asyncio
import json

import pytest

from message_gateway.adapters.codex import format_messages
from message_gateway.core.models import JsonValue, Message
from message_gateway.core.service import Service, Target
from tests.helpers import FakeAdapter


@pytest.mark.parametrize(
    "channel",
    ["external", "telegram", "unknown-source", "\u041a\u0430\u043d\u0430\u043b \U0001f600"],
)
def test_channel_and_content_are_opaque(channel: str) -> None:
    async def scenario() -> None:
        adapter = FakeAdapter()
        service = Service({"destination": Target(adapter)})
        value: JsonValue = {
            "text": {"unexpected": [1]},
            "photo": "not parsed",
            "attachments": {"path": "/does/not/exist"},
            "nested": [True, None],
        }
        message = service.submit(
            "destination", channel, value, sender="name", preview="Readable preview"
        )
        await service.tick()
        assert adapter.batches == [[message]]
        params = format_messages("thread", [message])
        assert len(params["input"]) == 1
        text = params["input"][0]["text"]
        assert text.endswith("> Readable preview")
        assert "not parsed" not in text
        full = json.loads(params["additionalContext"]["gateway_packet"]["value"])["messages"][0]
        assert full["content"] == value
        assert full["channel"] == channel
        region = params["input"][0]["text_elements"][0]["byteRange"]
        label = text.encode()[region["start"] : region["end"]].decode()
        assert label.startswith("[received ") and label.endswith("] name@" + channel + ">")

    asyncio.run(scenario())


def test_display_metadata_validation() -> None:
    service = Service({"work": Target(FakeAdapter())})
    for sender in ["", "x" * 129, "\ud800"]:
        with pytest.raises(ValueError):
            service.submit("work", "source", "x", sender=sender)
    for preview in ["x" * 257, "\ud800"]:
        with pytest.raises(ValueError):
            service.submit("work", "source", "x", preview=preview)
    for sent_at in ["bad", "2026-10-31T22:23:07", "x" * 65]:
        with pytest.raises(ValueError):
            service.submit("work", "source", "x", sent_at=sent_at)
    message = Message.create(
        "source", "message", sender="name", preview="", sent_at="2026-10-31T22:23:07+03:00"
    )
    assert (
        format_messages("thread", [message])["input"][0]["text"]
        == "[2026-10-31 19:23:07 UTC] name@source> "
    )
