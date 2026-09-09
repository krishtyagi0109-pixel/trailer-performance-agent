# 🎬 Trailer IQ — AI-Powered Movie Trailer Analytics

Built for the **Agentic Cinema: The Blockbuster Hackathon** — ClickHouse track.

Trailer IQ is an AI analytics assistant for a movie studio's marketing team. Ask a plain-English question — *"which director has the best audience sentiment?"* — and an agent writes real SQL, runs it live against real data in ClickHouse Cloud, and answers directly, with the exact SQL it used available on demand.

**Live demo:** https://trailer-iq.vercel.app*
**Backend API:** https://trailer-performance-agent.onrender.com*

---

## Why this isn't just a recommendation engine

Recommendation systems (like YouTube's) quietly rank content *for viewers*. Trailer IQ is a **decision-support tool for a business team** — it answers direct questions a marketer would actually ask, and shows the exact SQL it ran so the answer can be verified, not just trusted. It's an analyst replacing manual spreadsheet work, not an algorithm serving content to viewers.

---

## Architecture

```
┌──────────┐     ┌──────────────┐     ┌────────────┐     ┌───────────────────┐     ┌─────────────────┐
│ Browser  │────▶│   FastAPI    │────▶│   Gemini   │────▶│  mcp-clickhouse   │────▶│ ClickHouse Cloud│
│ (UI)     │◀────│  /ask        │◀────│  3.5 Flash │◀────│  (official MCP)   │◀────│  movie_trailers  │
└──────────┘     └──────────────┘     └────────────┘     └───────────────────┘     └─────────────────┘
```

**Every ClickHouse query goes through the official `mcp-clickhouse` MCP server** — never a direct SDK call — per the hackathon's ClickHouse track requirement. See `backend/app/mcp_client.py`. The backend keeps one persistent MCP session open for the life of the process, rather than spawning a new subprocess per query, to keep response times reasonable in production.

### How it works

1. User asks a plain-English question in the UI (e.g. *"Which director has the best audience sentiment?"*)
2. The **FastAPI backend** (`/ask`) sends the question, plus the `movie_trailers` schema, to **Gemini 3.5-Flash-Lite**
3. Gemini generates a SQL query tailored to that schema
4. The backend runs the query through the **official `mcp-clickhouse` MCP server** against **ClickHouse Cloud** — never a raw SDK call
5. The answer, along with the exact SQL used, is returned to the frontend — shown by default, with SQL available on demand via the "Show SQL used" toggle

---

## Data

We use one dataset: **`movie_trailers`** — real movie metadata, real YouTube trailer video IDs, and real audience sentiment extracted from actual trailer comments (source: Kaggle "Movies YouTube Trailers & Sentiment"). See [`SCHEMA.md`](./SCHEMA.md) for the full column reference.

Nothing in this table is fabricated or synthetic — every column is either real source data or a straightforward derived calculation (e.g. sentiment counts parsed from the source dataset). Trailer links are only constructed and shown when a user explicitly asks for one.

---

## Tech stack

| Layer | Technology |
|---|---|
| Reasoning agent | Gemini 3.5-Flash-Lite (via `google-genai` SDK), manual function-calling loop |
| Data layer | ClickHouse Cloud |
| Query bridge | Official `mcp-clickhouse` MCP server (persistent stdio session) |
| Backend | FastAPI (Python), deployed on Render |
| Frontend | HTML / Tailwind CDN / vanilla JS — no build step, deployed on Vercel |

---

## How the agent answers

- **Direct and concise.** No mention of "the database," "the dataset," or "the query returned N rows" — it states facts as if it already knew them.
- **No trailer links unless asked.** A link is only constructed and included when the question explicitly asks for one (e.g. "give me the trailer link for..."), using the real `video_id` in `movie_trailers`.
- **The SQL is always available, just not forced on you.** The frontend hides it behind a "Show SQL used" toggle so the answer stays the focus.

---

## Setup (local development)

### Backend
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your ClickHouse + Gemini credentials
python3 -m uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
python3 -m http.server 5500
# open http://localhost:5500/trailer-iq.html
```
Update `API_BASE` at the top of the HTML's `<script>` tag to point at your backend (`http://localhost:8000` locally, or your deployed backend URL in production).

---

## Deployment

- **Backend:** deployed on [Render](https://render.com) from this repo's `backend/` directory using the included `Dockerfile`. Environment variables (`CLICKHOUSE_HOST`, `CLICKHOUSE_PASSWORD`, `GEMINI_API_KEY`, etc.) are set in Render's dashboard, not committed to the repo.
- **Frontend:** deployed on [Vercel](https://vercel.com) as a single static HTML file.

---

## API

### `POST /ask`
```bash
curl -X POST https://your-backend.onrender.com/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Which director has the best audience sentiment?"}'
```
Returns `{ "answer": "...", "sql_used": "SELECT ..." }`.

### `GET /health`
Confirms ClickHouse connectivity via MCP and returns `{"status": "healthy", "clickhouse_connected": true, "mcp_available": true}` when everything is working.

---

## Example questions to try

- "Which director has the best audience sentiment?"
- "List movies with a budget over $100M"
- "Which movies had the most negative audience sentiment?"
- "What's the average runtime for R-rated movies?"
- "Give me the trailer link for Lady Bird"

---

## Known limitations

- **Free-tier hosting cold starts.** If the backend has been idle, the first request after a while can take longer while the container spins up and the MCP session reconnects — this is a hosting-tier characteristic, not an application bug.
- **Gemini API availability.** Like any hosted LLM API, Gemini occasionally returns a transient `503 UNAVAILABLE` under high demand. The backend surfaces this clearly rather than failing silently; retrying after a short wait resolves it.
- Single MCP session per backend instance — appropriate for a demo, not built for high concurrent load.

---

## License

MIT — see `LICENSE`.


## 𝗥𝗼𝗹𝗲𝘀

**The Director and Producer** — Krish Tyagi
**The Creative Head and Production Designer** — Kriti Garg
 
