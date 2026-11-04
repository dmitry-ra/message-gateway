"""Shared application operations for REST and MCP, without HTTP round trips."""

from dataclasses import asdict
from typing import Any

from message_gateway.core.service import QueueFull, Service
from message_gateway.interfaces.models import Submission


class RequestError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Operations:
    def __init__(self, service: Service) -> None:
        self.service = service

    def require_target(self, target: str) -> None:
        if target not in self.service.targets:
            raise RequestError(404, "Unknown destination")

    def list_targets(self) -> dict[str, object]:
        return self.service.status()

    def send_message(self, target: str, submission: Submission) -> dict[str, Any]:
        self.require_target(target)
        try:
            message = self.service.submit(
                target,
                submission.channel,
                submission.content,
                sender=submission.sender,
                preview=submission.preview,
                sent_at=submission.sent_at,
            )
        except ValueError as error:
            raise RequestError(400, str(error)) from None
        except QueueFull as error:
            raise RequestError(429, str(error)) from None
        return asdict(message)
