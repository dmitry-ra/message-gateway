import hashlib
import json
from dataclasses import asdict

import pytest

from message_gateway.adapters.codex import format_messages
from message_gateway.core.models import Message


@pytest.mark.parametrize(
    "content",
    [
        "x" * 12000,
        "\u041f\u0440\u0438\u0432\u0435\u0442 \U0001f600" * 1000,
        {"nested": ["BEGIN", "middle" * 1500, "END"]},
    ],
)
def test_full_packet_survives_receiver_per_entry_limit(content: object) -> None:
    # Deserialize as a client would to keep the fixture's type validation separate.
    message = Message.create("arbitrary-source", json.loads(json.dumps(content)))
    contexts = format_messages("thread", [message])["additionalContext"]
    assert all(len(entry["value"].encode()) < 4000 for entry in contexts.values())
    manifest = json.loads(contexts["gateway_packet"]["value"])
    assert manifest["format"] == "json_chunks"
    fragments = []
    for index in range(manifest["count"]):
        entry = contexts[f"gateway_part_{index:04d}"]
        assert entry["kind"] == "untrusted"
        fragments.append(entry["value"])
    reconstructed = "".join(fragments)
    assert hashlib.sha256(reconstructed.encode()).hexdigest() == manifest["sha256"]
    assert json.loads(reconstructed) == {"messages": [asdict(message)]}
