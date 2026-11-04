"""Strict input shapes; opaque content and domain limits belong to the core."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Submission(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    channel: str
    content: JsonValue = Field(...)
    sender: str | None = None
    preview: str | None = None
    sent_at: str | None = None


class ToolSubmission(Submission):
    target: str


class EmptyArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
