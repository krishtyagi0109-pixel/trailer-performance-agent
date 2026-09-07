"""
Pydantic request/response schemas for the Trailer Performance Agent API.
These models define the contract for all API endpoints, ensuring
consistent validation and serialization.
"""
from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Request Models
# =============================================================================
class AskRequest(BaseModel):
    """Request body for POST /ask -- a natural-language question about trailers."""
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The natural-language question to ask about trailer performance",
        examples=["Which trailer has the highest completion rate?"],
    )

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, v: str) -> str:
        """Reject whitespace-only questions."""
        if not v.strip():
            raise ValueError("Question must not be empty or whitespace-only")
        return v.strip()


# =============================================================================
# Response Models
# =============================================================================
class AskResponse(BaseModel):
    """Response from POST /ask -- the agent's answer with supporting metadata."""
    answer: str = Field(
        ...,
        description="The agent's natural-language answer to the question",
    )
    reasoning_steps: list[str] = Field(
        default_factory=list,
        description="Step-by-step reasoning the agent performed",
    )
    sql_used: str | None = Field(
        default=None,
        description="The SQL query(ies) executed against ClickHouse via MCP",
    )
    visualization: dict | None = Field(
        default=None,
        description="Chart/table spec produced by decide_visualization, if any",
    )


class HealthResponse(BaseModel):
    """Response from GET /health -- system status overview."""
    status: str = Field(
        ...,
        description="Overall system status: 'healthy' or 'degraded'",
    )
    clickhouse_connected: bool = Field(
        ...,
        description="Whether MCP can reach ClickHouse",
    )
    mcp_available: bool = Field(
        ...,
        description="Whether the mcp-clickhouse server can be spawned",
    )


class TrailerRecord(BaseModel):
    """A single row from the trailer_stats table."""
    trailer_id: str
    upload_date: str
    video_duration_min: float
    avg_view_duration_sec: float
    avg_view_percentage: float
    ctr_percentage: float
    impressions: int
    comments: int
    total_watch_time_hours: float
    content_category: str


class TrailersResponse(BaseModel):
    """Response from GET /trailers -- a list of trailer records."""
    trailers: list[TrailerRecord] = Field(
        ...,
        description="List of trailer records from ClickHouse",
    )
    count: int = Field(
        ...,
        description="Number of trailers returned",
    )


class ErrorResponse(BaseModel):
    """Standardized error response body."""
    error: str = Field(
        ...,
        description="Human-readable error message",
    )
    detail: str | None = Field(
        default=None,
        description="Additional error context (e.g., stack trace hint)",
    )

