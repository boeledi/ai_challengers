"""Pydantic models for API requests and responses."""

from pydantic import BaseModel, Field


class DeliberateRequest(BaseModel):
    """Request to start a deliberation."""
    question: str = Field(..., min_length=1, max_length=10000)
    mode: str = Field(default="council")
    depth: str | None = Field(default=None)
    length: str | None = Field(default=None)
    rounds: int = Field(default=1, ge=1, le=10)
    no_interact: bool = Field(default=False)
    chairman: str | None = Field(default=None)
    files: list[str] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """Request to start an analysis."""
    source: str = Field(..., min_length=1)
    with_qa: bool = Field(default=False)
    qa_count: int = Field(default=10, ge=1, le=50)
    lang: str | None = Field(default=None)
    extract: bool = Field(default=False)
    length: str | None = Field(default=None)


class InteractionAnswer(BaseModel):
    """User's answer to mid-pipeline questions."""
    answer: str = Field(..., min_length=1)


class ConfigKeyUpdate(BaseModel):
    """Update an API key."""
    key_name: str
    key_value: str


class SessionSummary(BaseModel):
    """Brief session info for list views."""
    id: str
    created_at: str
    question: str
    mode: str
    depth: str
    length: str
    status: str
    duration_ms: int | None = None
    total_cost: float | None = None
