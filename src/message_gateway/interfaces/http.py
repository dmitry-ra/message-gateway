"""REST routes backed by the same operations as MCP."""

from importlib.resources import files

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from message_gateway.interfaces.models import Submission
from message_gateway.interfaces.operations import Operations, RequestError


def routes(operations: Operations) -> list[Route]:
    async def targets(request: Request) -> JSONResponse:
        return JSONResponse(operations.list_targets())

    async def submit(request: Request) -> JSONResponse:
        try:
            target = request.path_params["target"]
            operations.require_target(target)
            submission = Submission.model_validate_json(await request.body())
            result = operations.send_message(target, submission)
        except ValidationError:
            return JSONResponse({"error": "Invalid submission"}, status_code=400)
        except RequestError as error:
            return JSONResponse({"error": str(error)}, status_code=error.status)
        return JSONResponse(result, status_code=202)

    async def specification(request: Request) -> Response:
        content = files("message_gateway.interfaces").joinpath("openapi.json").read_text()
        return Response(content, media_type="application/json")

    return [
        Route("/v1/targets", targets),
        Route("/v1/targets/{target}/messages", submit, methods=["POST"]),
        Route("/openapi.json", specification),
    ]
