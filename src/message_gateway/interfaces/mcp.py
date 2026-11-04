"""Official MCP SDK tools calling shared operations in the daemon process."""

from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools import Tool
from mcp.types import CallToolResult, TextContent
from pydantic import JsonValue, ValidationError

from message_gateway.interfaces.models import EmptyArguments, Submission, ToolSubmission
from message_gateway.interfaces.operations import Operations


class ValidateArguments(ServerMiddleware[Any]):
    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method == "tools/call" and ctx.params:
            name = ctx.params.get("name")
            arguments = ctx.params.get("arguments", {})
            try:
                if name == "send_message":
                    ToolSubmission.model_validate(arguments)
                elif name == "list_targets":
                    EmptyArguments.model_validate(arguments)
            except ValidationError:
                return CallToolResult(
                    is_error=True, content=[TextContent(type="text", text="Invalid tool arguments")]
                )
        return await call_next(ctx)


def create_server(operations: Operations) -> MCPServer[Any]:
    async def list_targets() -> dict[str, Any]:
        """List configured destinations, readiness, queues and last delivery result."""
        return operations.list_targets()

    async def send_message(
        target: str,
        channel: str,
        content: JsonValue,
        sender: str | None = None,
        preview: str | None = None,
        sent_at: str | None = None,
    ) -> dict[str, Any]:
        """Queue opaque text/JSON in this process. Acceptance is not durable or completion."""
        return operations.send_message(
            target,
            Submission(
                channel=channel,
                content=content,
                sender=sender,
                preview=preview,
                sent_at=sent_at,
            ),
        )

    listed = Tool.from_function(list_targets)
    listed.parameters = EmptyArguments.model_json_schema()
    submitted = Tool.from_function(send_message)
    submitted.parameters = ToolSubmission.model_json_schema()
    return MCPServer("message-gateway", tools=[listed, submitted], middleware=[ValidateArguments()])
