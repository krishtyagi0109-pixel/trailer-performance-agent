# 🎬 Trailer Performance Agent — Backend

> AI-powered media analytics assistant that answers natural-language questions
> about trailer engagement data using **Gemini** and **ClickHouse** (via the
> official **MCP protocol**).

Built for the [Google Cloud "Agentic Cinema" Hackathon](https://clickhouse.com) — ClickHouse Track.

---

## Architecture

```
┌──────────┐     ┌──────────────┐     ┌────────────┐     ┌───────────────────┐     ┌─────────────────┐
│  Client   │────▶│   FastAPI    │────▶│   Gemini   │────▶│  mcp-clickhouse   │────▶│ ClickHouse Cloud│
│ (curl/UI) │◀────│  /ask, etc.  │◀────│  Agent     │◀────│  (stdio MCP)      │◀────│  trailer_stats  │
└──────────┘     └──────────────┘     └────────────┘     └───────────────────┘     └─────────────────┘
```

**Key**: All ClickHouse queries go through the official `mcp-clickhouse` MCP server — never direct SDK calls.

---

## Prerequisites

| Requirement         | Version  | Notes                                    |
| ------------------- | -------- | ---------------------------------------- |
| Python              | ≥ 3.10   |                                          |
| `uv`                | latest   | [Install uv](https://github.com/astral-sh/uv) — used to run `mcp-clickhouse` |
| ClickHouse Cloud    | —        | With `trailer_stats` table populated     |
| Gemini API Key      | —        | [Get one here](https://aistudio.google.com/apikey) |

---

## Quick Start

### 1. Clone & navigate

```bash
cd trailer-performance-agent/backend
```

### 2. Create environment file

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Verify `uv` is installed

```bash
uv --version
# If not installed: pip install uv
```

### 5. Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

The API is now live at `http://localhost:8000`.

---

## API Endpoints

### `GET /health` — Health Check

Verifies MCP server availability and ClickHouse connectivity.

```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "healthy",
  "clickhouse_connected": true,
  "mcp_available": true
}
```

---

### `GET /trailers` — List Trailers

Fetches up to 100 trailer records (for frontend dropdowns/testing).

```bash
curl http://localhost:8000/trailers
```

**Response:**
```json
{
  "trailers": [
    {
      "trailer_id": "VID_00001",
      "upload_date": "2024-03-15",
      "video_duration_min": 2.5,
      "avg_view_duration_sec": 95.3,
      "avg_view_percentage": 63.5,
      "ctr_percentage": 4.2,
      "impressions": 150000,
      "comments": 342,
      "total_watch_time_hours": 1250.0,
      "content_category": "Action"
    }
  ],
  "count": 100
}
```

---

### `POST /ask` — Ask a Question

Submit a natural-language question. The agent queries ClickHouse via MCP,
reasons with Gemini, and returns an answer with the SQL used.

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Which trailer has the highest completion rate?"}'
```

**Response:**
```json
{
  "answer": "The trailer with the highest completion rate is VID_12345 with an average view percentage of 95.2%. It belongs to the Drama category and was uploaded on 2024-06-01.",
  "reasoning_steps": [
    "Executing SQL: SELECT trailer_id, avg_view_percentage, content_category, upload_date FROM default.trailer_stats ORDER BY avg_view_percentage DESC LIMIT 1",
    "Tool 'run_query' returned data successfully",
    "Generated final answer"
  ],
  "sql_used": "SELECT trailer_id, avg_view_percentage, content_category, upload_date FROM default.trailer_stats ORDER BY avg_view_percentage DESC LIMIT 1"
}
```

---

## Example Questions to Try

| Question | What it tests |
|----------|---------------|
| `"Which trailer has the highest completion rate?"` | Top performer by avg_view_percentage |
| `"Compare Action vs Drama trailers on average CTR and completion rate"` | Category comparison with aggregations |
| `"Find trailers with CTR above 8% but completion rate below 30%"` | Anomaly detection (misleading trailers) |
| `"Compare trailer VID_00001 and VID_00050 side by side"` | Direct trailer comparison |
| `"What are the top 5 content categories by total watch time?"` | Category-level aggregation |
| `"Show me trailers uploaded in the last 30 days sorted by impressions"` | Time-based filtering |

---

## Project Structure

```
backend/
├── app/
│   ├── __init__.py       # Package marker
│   ├── main.py           # FastAPI app — endpoints, CORS, error handling
│   ├── config.py         # Environment variable loading via pydantic-settings
│   ├── models.py         # Pydantic request/response schemas
│   ├── mcp_client.py     # MCP client — spawns mcp-clickhouse via stdio
│   └── agent.py          # Agent loop — Gemini ↔ MCP orchestration
├── .env.example          # Environment variable template
├── .gitignore            # Excludes .env, __pycache__, etc.
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

---

## How It Works

1. **User asks a question** via `POST /ask`
2. **FastAPI** passes it to the **Agent** (`agent.py`)
3. **Agent** sends the question + system prompt + tool declarations to **Gemini**
4. **Gemini** decides to call `run_query(sql="SELECT ...")` as a function call
5. **Agent** intercepts the function call and routes it to the **MCP Client** (`mcp_client.py`)
6. **MCP Client** spawns `mcp-clickhouse` as a subprocess, opens a stdio MCP session, and executes the query
7. **mcp-clickhouse** queries **ClickHouse Cloud** over HTTPS and returns the result
8. **Agent** feeds the result back to **Gemini** as a `FunctionResponse`
9. **Gemini** analyzes the data and produces a natural-language answer (or requests another query)
10. **Agent** returns the answer, reasoning steps, and SQL used

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `MCPConnectionError` | Check that `uv` is installed and on PATH. Verify ClickHouse credentials in `.env`. |
| `AgentError: Failed to get response from Gemini` | Verify your `GEMINI_API_KEY` is valid. Check API quota. |
| `422 Validation Error` on `/ask` | Ensure the request body has a non-empty `"question"` field. |
| Slow first request | The first MCP call downloads `mcp-clickhouse` via `uv`. Subsequent calls are cached. |
| Empty results from `/trailers` | Verify the `CLICKHOUSE_DATABASE` env var matches where `trailer_stats` lives. |

---

## License

Built for the Google Cloud "Agentic Cinema" Hackathon.
