import json
from pathlib import Path

from message_gateway.adapters.codex import packet_context
from message_gateway.core.models import JsonValue, Message


def test_independent_message_items_need_no_json_reconstruction(tmp_path: Path) -> None:
    messages = [Message.create("duty", {"text": "observation " * 40, "seq": i}) for i in range(20)]
    contexts = packet_context(messages, tmp_path)
    manifest = json.loads(contexts["gateway_packet"]["value"])
    assert manifest["format"] == "message_items"
    assert len(manifest["items"]) == 20
    for i, name in enumerate(manifest["items"]):
        assert json.loads(contexts[name]["value"])["content"] == messages[i].content
        assert len(contexts[name]["value"].encode()) <= 3000


def test_large_message_document_retains_full_unicode_and_type(tmp_path: Path) -> None:
    content: JsonValue = {
        "text": "\u041f\u0440\u0438\u0432\u0435\u0442\U0001f600" * 2000,
        "null": None,
        "flag": False,
    }
    message = Message.create("opaque", content)
    contexts = packet_context([message], tmp_path)
    manifest = json.loads(contexts["gateway_packet"]["value"])
    item = json.loads(contexts[manifest["items"][0]]["value"])
    path = Path(item["content_file"])
    assert path.parent == tmp_path
    assert json.loads(path.read_text())["content"] == content
    assert all(len(v["value"].encode()) <= 3000 for v in contexts.values())
