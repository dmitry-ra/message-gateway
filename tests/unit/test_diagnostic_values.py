import json
from pathlib import Path

import pytest

from message_gateway.adapters.codex import CodexAdapter, diagnostic_error_info, thread_readiness


@pytest.mark.parametrize(
    ("value", "expected", "status"),
    [
        ("serverOverloaded", "serverOverloaded", None),
        ("server_overloaded", "serverOverloaded", None),
        ("SECRET_BODY", None, None),
        (["SECRET_BODY"], None, None),
        ({"SECRET_BODY": {"httpStatusCode": 503}}, None, None),
        (
            {"httpConnectionFailed": {"httpStatusCode": 503, "body": "SECRET_BODY"}},
            ["httpConnectionFailed"],
            503,
        ),
        ({"httpConnectionFailed": {"httpStatusCode": True}}, ["httpConnectionFailed"], None),
        ({"httpConnectionFailed": {"httpStatusCode": 999}}, ["httpConnectionFailed"], None),
        ({"httpConnectionFailed": None}, ["httpConnectionFailed"], None),
    ],
)
def test_error_diagnostics_keep_only_known_classes_and_http_status(
    value: object, expected: object, status: int | None
) -> None:
    assert diagnostic_error_info(value) == (expected, status)


def test_notification_diagnostics_sanitize_other_source_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="message_gateway.diagnostics")
    adapter = CodexAdapter("thread", tmp_path / "unused")
    adapter._notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread",
                "turnId": "SECRET BODY",
                "willRetry": "SECRET_BODY",
                "turn": {"status": "SECRET_BODY", "error": {"codexErrorInfo": ["SECRET_BODY"]}},
            },
        }
    )
    adapter._observe_state(
        {"type": "SECRET_BODY", "activeFlags": ["SECRET_BODY", {}]}, "notification"
    )
    assert "SECRET" not in caplog.text
    events = [json.loads(record.message) for record in caplog.records]
    assert events[0]["turn_id"] is None
    assert events[0]["status"] is None
    assert events[0]["will_retry"] is None
    assert events[1]["state"] == "unknown" and events[1]["active_flags"] == []


@pytest.mark.parametrize("state", ["", " ", "\t\n"])
def test_whitespace_state_is_unknown(state: str) -> None:
    from message_gateway.core.models import DeliveryUnknown

    with pytest.raises(DeliveryUnknown):
        thread_readiness({"status": {"type": state}})
