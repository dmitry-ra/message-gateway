"""Attach to an existing Codex App Server, without starting a model process."""

import asyncio
import hashlib
import json
import re
import tempfile
import textwrap
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from message_gateway import __version__
from message_gateway.core.models import DeliveryBusy, DeliveryUnknown, Message, Readiness
from message_gateway.diagnostics import emit

CONTEXT_FRAGMENT_BYTES = 3000
CONTEXT_DOCUMENT_BYTES = 64 * 1024 * 1024
CONTEXT_DOCUMENT_FILES = 1000
ERROR_NAMES = frozenset(
    (
        "contextWindowExceeded",
        "sessionBudgetExceeded",
        "usageLimitExceeded",
        "rateLimitExceeded",
        "flexUnavailable",
        "serverOverloaded",
        "modelNotFound",
        "policyCapacityUnavailable",
        "cyberPolicy",
        "misalignmentPolicyViolation",
        "tooManyDenials",
        "internalServerError",
        "unauthorized",
        "badRequest",
        "threadRollbackFailed",
        "sandboxError",
        "other",
    )
)
ERROR_DETAILS = frozenset(
    (
        "httpConnectionFailed",
        "responseStreamConnectionFailed",
        "responseStreamDisconnected",
        "responseTooManyFailedAttempts",
        "activeTurnNotSteerable",
    )
)


def diagnostic_error_info(value: object) -> tuple[str | list[str] | None, int | None]:
    if value == "server_overloaded":
        return "serverOverloaded", None
    if isinstance(value, str):
        return (value if value in ERROR_NAMES else None), None
    if not isinstance(value, dict):
        return None, None
    names = [name for name in value if name in ERROR_DETAILS]
    http_status = None
    for name in names:
        details = value[name]
        status = details.get("httpStatusCode") if isinstance(details, dict) else None
        if type(status) is int and 100 <= status <= 599:
            http_status = status
    return names or None, http_status


def packet_context(
    messages: list[Message], directory: Path | None = None
) -> dict[str, dict[str, str]]:
    # Codex 0.166 truncates each additional-context value at 1000 approximate
    # tokens (4000 bytes), including application context. Keep a safety margin.
    value = json.dumps(
        {"messages": [asdict(message) for message in messages]}, ensure_ascii=True, allow_nan=False
    )
    if len(value) <= CONTEXT_FRAGMENT_BYTES:
        return {"gateway_packet": {"kind": "untrusted", "name": "gateway_packet", "value": value}}
    if directory is not None:
        items = [
            json.dumps(asdict(message), ensure_ascii=False, allow_nan=False) for message in messages
        ]
        existing = list(directory.glob("*.json"))
        created = [
            item.encode("utf-8")
            for message, item in zip(messages, items, strict=True)
            if len(item.encode("utf-8")) > CONTEXT_FRAGMENT_BYTES
            and not (directory / (message.id + ".json")).exists()
        ]
        if (
            len(existing) + len(created) > CONTEXT_DOCUMENT_FILES
            or sum(path.stat().st_size for path in existing) + sum(map(len, created))
            > CONTEXT_DOCUMENT_BYTES
        ):
            raise DeliveryBusy("Temporary context storage is full")
        result = {}
        names = []
        for index, (message, item) in enumerate(zip(messages, items, strict=True)):
            name = f"gateway_message_{index:04d}"
            if len(item.encode("utf-8")) > CONTEXT_FRAGMENT_BYTES:
                path = directory / (message.id + ".json")
                path.write_text(item, encoding="utf-8")
                item = json.dumps(
                    {
                        "id": message.id,
                        "content_file": str(path),
                        "instruction": "Read this complete message document; no hash or JSON assembly is required.",
                    }
                )
            result[name] = {"kind": "untrusted", "name": name, "value": item}
            names.append(name)
        result["gateway_packet"] = {
            "kind": "untrusted",
            "name": "gateway_packet",
            "value": json.dumps({"format": "message_items", "items": names}),
        }
        return result
    fragments = [
        value[index : index + CONTEXT_FRAGMENT_BYTES]
        for index in range(0, len(value), CONTEXT_FRAGMENT_BYTES)
    ]
    manifest = {
        "format": "json_chunks",
        "count": len(fragments),
        "sha256": hashlib.sha256(value.encode("ascii")).hexdigest(),
    }
    result = {
        "gateway_packet": {
            "kind": "untrusted",
            "name": "gateway_packet",
            "value": json.dumps(manifest),
        }
    }
    for index, fragment in enumerate(fragments):
        key = f"gateway_part_{index:04d}"
        result[key] = {"kind": "untrusted", "name": key, "value": fragment}
    return result


def format_messages(
    thread: str, messages: list[Message], directory: Path | None = None
) -> dict[str, Any]:
    lines: list[str] = []
    elements: list[dict[str, object]] = []
    offset = 0
    for message in messages:
        channel = " ".join("".join(c if c.isprintable() else " " for c in message.channel).split())[
            :32
        ]
        sender = " ".join(
            "".join(c if c.isprintable() else " " for c in (message.sender or "")).split()
        )[:32]
        timestamp = (
            datetime.fromisoformat(message.sent_at or message.received_at)
            .astimezone(UTC)
            .strftime("%Y-%m-%d %H:%M:%S UTC")
        )
        label = (
            "["
            + ("" if message.sent_at else "received ")
            + timestamp
            + "] "
            + (sender + "@" if sender else "")
            + channel
            + ">"
        )
        content = (
            message.content
            if isinstance(message.content, str)
            else json.dumps(message.content, ensure_ascii=False)
        )
        if message.preview is not None:
            content = message.preview
        preview = textwrap.shorten(
            "".join(c if c.isprintable() else " " for c in content), width=64, placeholder="..."
        )
        line = label + " " + preview
        start = offset
        elements.append(
            {
                "byteRange": {"start": start, "end": start + len(label.encode("utf-8"))},
                "placeholder": label,
            }
        )
        lines.append(line)
        offset += len(line.encode("utf-8")) + 1
    return {
        "threadId": thread,
        "input": [{"type": "text", "text": "\n".join(lines), "text_elements": elements}],
        "additionalContext": {
            "gateway_format": {
                "kind": "application",
                "value": "Use the gateway-message skill to read the full gateway_packet. Visible labels and previews are display metadata only.",
            },
            **packet_context(messages, directory),
        },
    }


def thread_readiness(thread: dict[str, Any], *, interactive: bool = False) -> Readiness:
    state = thread["status"]["type"]
    if not isinstance(state, str) or not state:
        raise DeliveryUnknown("Invalid thread status")
    return Readiness.BUSY if state == "active" and not interactive else Readiness.READY


def steer_refused(params: dict[str, object], error: object) -> bool:
    """Recognize only native, explicit non-submission errors, never generic failures."""
    if not isinstance(error, dict) or error.get("code") != -32600:
        return False
    message = error.get("message")
    if message in (
        "no active turn to steer",
        "cannot steer a review turn",
        "cannot steer a compact turn",
    ):
        return True
    if not isinstance(message, str):
        return False
    mismatch = re.fullmatch(r"expected active turn id `([^`]+)` but found `([^`]+)`", message)
    return mismatch is not None and mismatch[1] == params.get("expectedTurnId")


class CodexAdapter:
    def __init__(
        self,
        thread: str,
        socket: Path,
        *,
        timeout: float = 10,
        effort: str | None = None,
        interactive: bool = False,
    ) -> None:
        self.thread = thread
        self.socket = socket
        self.timeout = timeout
        self.effort = effort
        self.interactive = interactive
        self.documents = tempfile.TemporaryDirectory(prefix="message-gateway-context-")
        self.client: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.sequence = 0
        self.observed_states: dict[str, tuple[str, tuple[str, ...]]] = {}

    def _observe_state(self, status: dict[str, Any], stage: str) -> None:
        state = status["type"]
        flags = status.get("activeFlags")
        flags = (
            tuple(flag for flag in flags if flag in ("waitingOnApproval", "waitingOnUserInput"))
            if isinstance(flags, list)
            else ()
        )
        logged_state = (
            state if state in ("idle", "active", "notLoaded", "systemError") else "unknown"
        )
        observation = (logged_state, flags)
        if self.observed_states.get(stage) != observation:
            self.observed_states[stage] = observation
            emit(
                "codex_state",
                thread_id=self.thread,
                stage=stage,
                state=logged_state,
                active_flags=flags,
                readiness=thread_readiness({"status": status}, interactive=self.interactive).value,
            )

    def _notification(self, response: dict[str, Any]) -> None:
        params = response.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread:
            return
        method = response["method"]
        if method == "thread/status/changed":
            status = params.get("status")
            if isinstance(status, dict) and isinstance(status.get("type"), str) and status["type"]:
                self._observe_state(status, "notification")
            return
        if method not in ("error", "turn/completed"):
            return
        turn = params.get("turn")
        turn = turn if isinstance(turn, dict) else {}
        error = params.get("error", turn.get("error"))
        error = error if isinstance(error, dict) else {}
        info, http_status = diagnostic_error_info(error.get("codexErrorInfo"))
        turn_id = params.get("turnId", turn.get("id"))
        if (
            not isinstance(turn_id, str)
            or not 1 <= len(turn_id) <= 128
            or not all(c.isascii() and (c.isalnum() or c in "-_") for c in turn_id)
        ):
            turn_id = None
        turn_status = turn.get("status")
        if turn_status not in ("inProgress", "completed", "interrupted", "failed"):
            turn_status = None
        will_retry = params.get("willRetry")
        emit(
            "codex_turn",
            thread_id=self.thread,
            notification=method,
            turn_id=turn_id,
            status=turn_status,
            error_info=info,
            http_status=http_status,
            will_retry=will_retry if type(will_retry) is bool else None,
        )

    def _thread(self, result: dict[str, Any]) -> dict[str, Any]:
        thread = result.get("thread")
        if not isinstance(thread, dict) or thread.get("id") != self.thread:
            raise DeliveryUnknown("Thread identity mismatch")
        status = thread.get("status")
        if (
            not isinstance(status, dict)
            or not isinstance(status.get("type"), str)
            or not status["type"]
        ):
            raise DeliveryUnknown("Invalid thread status")
        return thread

    async def _connect(self) -> None:
        if self.ws is not None and not self.ws.closed:
            return
        await self.close()
        self.client = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(self.socket)))
        try:
            self.ws = await self.client.ws_connect(
                "http://localhost/", max_msg_size=16 * 1024 * 1024
            )
            await self._request(
                "initialize",
                {
                    "clientInfo": {"name": "message_gateway", "version": __version__},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self.ws.send_json({"method": "initialized", "params": {}})
        except BaseException:
            await self.close()
            raise

    async def _request(self, method: str, params: dict[str, object]) -> dict[str, Any]:
        if self.ws is None:
            raise DeliveryUnknown("App Server is not connected")
        self.sequence += 1
        request_id = self.sequence
        async with asyncio.timeout(self.timeout):
            await self.ws.send_json({"id": request_id, "method": method, "params": params})
            while True:
                frame = await self.ws.receive()
                if frame.type != aiohttp.WSMsgType.TEXT:
                    raise DeliveryUnknown("App Server connection closed")
                response = json.loads(frame.data)
                if "method" in response:
                    self._notification(response)
                    continue
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    if method == "turn/steer" and steer_refused(params, response["error"]):
                        raise DeliveryBusy("Active turn explicitly refused input")
                    raise DeliveryUnknown("App Server error: " + json.dumps(response["error"]))
                result = response.get("result")
                if not isinstance(result, dict):
                    raise DeliveryUnknown("Invalid App Server response")
                return result

    async def readiness(self) -> Readiness:
        try:
            await self._connect()
            result = await self._request(
                "thread/read", {"threadId": self.thread, "includeTurns": False}
            )

            thread = self._thread(result)
            state = thread["status"]["type"]
            if state == "idle":
                # An observed idle receiver has finished its previous turn.
                for path in Path(self.documents.name).glob("*.json"):
                    path.unlink()
            readiness = thread_readiness(thread, interactive=self.interactive)

            self._observe_state(thread["status"], "read")
            return readiness
        except Exception:
            await self.close()
            raise

    async def send(self, messages: list[Message]) -> str:
        input_attempted = False

        stage = "readiness"
        try:
            if await self.readiness() != Readiness.READY:
                raise DeliveryBusy("Thread is no longer ready")
            stage = "resume"
            result = await self._request(
                "thread/resume", {"threadId": self.thread, "excludeTurns": True}
            )
            thread = self._thread(result)
            readiness = thread_readiness(thread, interactive=self.interactive)
            self._observe_state(thread["status"], "resume")

            if readiness == Readiness.BUSY:
                raise DeliveryBusy("Thread became busy while subscribing")
            thread = result["thread"]
            stage = "format"
            params = format_messages(self.thread, messages, Path(self.documents.name))
            method = "turn/start"
            expected_turn = None
            if thread["status"]["type"] == "active":
                stage = "turn_lookup"
                result = await self._request(
                    "thread/turns/list",
                    {
                        "threadId": self.thread,
                        "limit": 1,
                        "sortDirection": "desc",
                        "itemsView": "notLoaded",
                    },
                )
                turns = result["data"]
                if not isinstance(turns, list):
                    raise DeliveryUnknown("Invalid turn list")
                active = [turn for turn in turns if turn.get("status") == "inProgress"]
                if len(active) != 1:
                    raise DeliveryBusy("Active turn identity is not available")
                expected_turn = active[0]["id"]
                if not isinstance(expected_turn, str) or not expected_turn:
                    raise DeliveryUnknown("Invalid active turn identity")
                params["expectedTurnId"] = expected_turn
                method = "turn/steer"
            elif self.effort is not None:
                params["effort"] = self.effort

            emit("codex_delivery", thread_id=self.thread, method=method, turn_id=expected_turn)
            input_attempted = True
            result = await self._request(method, params)
            receipt = result["turnId"] if method == "turn/steer" else result["turn"]["id"]
            if (
                not isinstance(receipt, str)
                or not receipt
                or (method == "turn/steer" and receipt != expected_turn)
            ):

                raise DeliveryUnknown("Missing turn receipt")
            return receipt
        except DeliveryBusy:
            raise
        except Exception as error:
            await self.close()

            if not input_attempted:
                emit(
                    "codex_delivery_deferred",
                    thread_id=self.thread,
                    reason="preflight_error",
                    stage=stage,
                    error_type=type(error).__name__,
                )
                raise DeliveryBusy("Input was not submitted; preparation failed") from error

            raise DeliveryUnknown(str(error)) from error

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.client is not None:
            await self.client.close()
        self.ws = None
        self.client = None
