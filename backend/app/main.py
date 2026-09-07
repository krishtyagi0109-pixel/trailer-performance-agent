"""
FastAPI application entrypoint for the Trailer Performance Agent.

Exposes three endpoints:
  - POST /ask       — Ask a natural-language question about trailer performance
  - GET  /health    — Health check (MCP + ClickHouse connectivity)
  - GET  /trailers  — List trailer records (for frontend dropdown/testing)

CORS is enabled for all origins (hackathon demo configuration).
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.agent import agent, AgentError, RateLimitError
from app.config import settings
from app.mcp_client import mcp_client, MCPConnectionError
from app.models import (
    AskRequest,
    AskResponse,
    ErrorResponse,
    HealthResponse,
    TrailerRecord,
    TrailersResponse,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Application Lifespan — startup/shutdown logic
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs on startup and shutdown.
    """
    logger.info("=" * 60)
    logger.info("Trailer Performance Agent — Starting up")
    logger.info("=" * 60)
    logger.info("ClickHouse host: %s", settings.clickhouse_host)
    logger.info("ClickHouse database: %s", settings.clickhouse_database)
    logger.info("Gemini model: %s", settings.gemini_model)
    logger.info("=" * 60)

    await mcp_client.connect()  # opens persistent MCP session + warms up ClickHouse

    yield  # Application runs here

    await mcp_client.disconnect()  # closes it on shutdown
    logger.info("Trailer Performance Agent — Shutting down")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="Trailer Performance Agent",
    description=(
        "AI-powered media analytics assistant that uses Gemini and ClickHouse "
        "(via MCP) to answer natural-language questions about trailer engagement data."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for all origins (hackathon demo — restrict in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# POST /ask — Main agent endpoint
# =============================================================================


@app.post(
    "/ask",
    response_model=AskResponse,
    responses={
        429: {"model": ErrorResponse, "description": "Gemini API rate/quota limit hit"},
        500: {"model": ErrorResponse, "description": "MCP or internal error"},
        502: {"model": ErrorResponse, "description": "Gemini API error"},
    },
    summary="Ask a question about trailer performance",
    description=(
        "Submit a natural-language question. The agent queries ClickHouse via "
        "the MCP protocol, reasons with Gemini, and returns an answer with "
        "the SQL query used and reasoning steps."
    ),
)
async def ask_question(request: AskRequest) -> AskResponse:
    """
    Process a natural-language question about trailer performance data.

    The agent loop:
    1. Sends the question to Gemini with ClickHouse tool declarations
    2. Gemini generates SQL queries as function calls
    3. Queries are executed via the official mcp-clickhouse server
    4. Results are fed back to Gemini for analysis
    5. Gemini produces a natural-language answer with cited data
    """
    logger.info("Received question: %s", request.question[:100])

    try:
        result = await agent.process_question(request.question)
        logger.info("Agent produced answer successfully")

        return AskResponse(
            answer=result["answer"],
            reasoning_steps=result["reasoning_steps"],
            sql_used=result["sql_used"],
	    visualization=result.get("visualization"),
        )

    except RateLimitError as e:
        logger.error("Gemini rate limit during /ask: %s", str(e))
        headers = {"Retry-After": str(int(e.retry_after))} if e.retry_after else None
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Gemini API rate limit exceeded",
                "detail": str(e),
            },
            headers=headers,
        )

    except MCPConnectionError as e:
        logger.error("MCP connection error during /ask: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "ClickHouse query failed via MCP",
                "detail": str(e),
            },
        )

    except AgentError as e:
        logger.error("Agent error during /ask: %s", str(e))
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Gemini agent error",
                "detail": str(e),
            },
        )

    except Exception as e:
        logger.exception("Unexpected error during /ask")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal server error",
                "detail": str(e),
            },
        )


# =============================================================================
# GET /health — Health check
# =============================================================================


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Verify system health: MCP server availability and ClickHouse connectivity.",
)
async def health_check() -> HealthResponse:
    """
    Check if the MCP server can be spawned and reach ClickHouse.
    Returns a structured health status.
    """
    clickhouse_ok = False
    mcp_ok = False

    try:
        clickhouse_ok = await mcp_client.health_check()
        mcp_ok = True  # If health_check() didn't throw, MCP is reachable
    except Exception as e:
        logger.warning("Health check failed: %s", str(e))

    status = "healthy" if (clickhouse_ok and mcp_ok) else "degraded"

    return HealthResponse(
        status=status,
        clickhouse_connected=clickhouse_ok,
        mcp_available=mcp_ok,
    )


# =============================================================================
# GET /trailers — List trailers (passthrough query for frontend/testing)
# =============================================================================


@app.get(
    "/trailers",
    response_model=TrailersResponse,
    responses={
        500: {"model": ErrorResponse, "description": "MCP or ClickHouse error"},
    },
    summary="List trailer records",
    description=(
        "Fetch trailer records from ClickHouse via MCP. "
        "Returns up to 100 rows for frontend dropdown/testing."
    ),
)
async def list_trailers() -> TrailersResponse:
    """
    Fetch trailer records directly from ClickHouse via MCP.
    Intended for populating a frontend dropdown or quick data inspection.
    """
    try:
        query = (
            f"SELECT * FROM {settings.clickhouse_database}.trailer_stats "
            f"ORDER BY upload_date DESC LIMIT 100"
        )
        raw_result = await mcp_client.execute_query(query)

        # Parse the MCP result into structured TrailerRecord objects.
        # mcp-clickhouse returns results as a text table or JSON depending
        # on the query. We attempt to parse as structured data.
        trailers = _parse_trailer_results(raw_result)

        return TrailersResponse(
            trailers=trailers,
            count=len(trailers),
        )

    except MCPConnectionError as e:
        logger.error("Failed to list trailers: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Failed to fetch trailers from ClickHouse via MCP",
                "detail": str(e),
            },
        )


def _parse_trailer_results(raw_result: str) -> list[TrailerRecord]:
    """
    Parse the raw text output from mcp-clickhouse into TrailerRecord objects.

    mcp-clickhouse returns results as JSON in the format:
    {"columns": ["col1", "col2", ...], "rows": [[val1, val2, ...], ...]}

    This function also handles JSON arrays and tab-separated formats as fallbacks.
    """
    if not raw_result or raw_result.strip() == "No results returned.":
        return []

    trailers = []

    # Try JSON parsing first
    try:
        data = json.loads(raw_result)

        # Handle mcp-clickhouse format: {"columns": [...], "rows": [[...]]}
        if isinstance(data, dict) and "columns" in data and "rows" in data:
            columns = [c.lower() for c in data["columns"]]
            for row_values in data["rows"]:
                try:
                    row_dict = dict(zip(columns, row_values))
                    trailers.append(
                        TrailerRecord(
                            trailer_id=str(row_dict.get("trailer_id", "")),
                            upload_date=str(row_dict.get("upload_date", "")),
                            video_duration_min=float(row_dict.get("video_duration_min", 0)),
                            avg_view_duration_sec=float(row_dict.get("avg_view_duration_sec", 0)),
                            avg_view_percentage=float(row_dict.get("avg_view_percentage", 0)),
                            ctr_percentage=float(row_dict.get("ctr_percentage", 0)),
                            impressions=int(float(row_dict.get("impressions", 0))),
                            comments=int(float(row_dict.get("comments", 0))),
                            total_watch_time_hours=float(row_dict.get("total_watch_time_hours", 0)),
                            content_category=str(row_dict.get("content_category", "")),
                        )
                    )
                except (ValueError, KeyError, TypeError) as e:
                    logger.warning("Skipping unparseable row: %s (error: %s)", row_values[:5], e)
                    continue
            return trailers

        # Handle simple JSON array of objects
        if isinstance(data, list):
            for row in data:
                try:
                    trailers.append(TrailerRecord(**row))
                except Exception as e:
                    logger.warning("Skipping unparseable row: %s (error: %s)", str(row)[:80], e)
            return trailers

    except (json.JSONDecodeError, TypeError):
        pass

    # Fall back to tab-separated parsing
    lines = raw_result.strip().split("\n")
    if len(lines) < 2:
        return []

    # First line is the header
    headers = [h.strip().lower() for h in lines[0].split("\t")]

    for line in lines[1:]:
        if not line.strip():
            continue
        values = line.split("\t")
        if len(values) != len(headers):
            continue

        try:
            row_dict = dict(zip(headers, values))
            trailers.append(
                TrailerRecord(
                    trailer_id=row_dict.get("trailer_id", ""),
                    upload_date=row_dict.get("upload_date", ""),
                    video_duration_min=float(row_dict.get("video_duration_min", 0)),
                    avg_view_duration_sec=float(row_dict.get("avg_view_duration_sec", 0)),
                    avg_view_percentage=float(row_dict.get("avg_view_percentage", 0)),
                    ctr_percentage=float(row_dict.get("ctr_percentage", 0)),
                    impressions=int(float(row_dict.get("impressions", 0))),
                    comments=int(float(row_dict.get("comments", 0))),
                    total_watch_time_hours=float(row_dict.get("total_watch_time_hours", 0)),
                    content_category=row_dict.get("content_category", ""),
                )
            )
        except (ValueError, KeyError) as e:
            logger.warning("Skipping unparseable row: %s (error: %s)", line[:80], e)
            continue

    return trailers


# =============================================================================
# Run with: uvicorn app.main:app --reload
# =============================================================================
