"""
Agent orchestrator for the Trailer Performance Agent.

This module implements the core agent loop:
    1. Receives a natural-language question from the user
    2. Sends it to Gemini with tool declarations (matching MCP tools)
    3. Intercepts function_call responses from Gemini
    4. Executes them via the MCP ClickHouse client
    5. Feeds results back to Gemini
    6. Repeats until Gemini produces a final text answer

The agent uses MANUAL function calling (not automatic) so we have full
control over the execution loop, can track SQL queries used, and can
collect reasoning steps for the API response.
"""

import logging
import re

from google import genai
from google.genai import types

from app.config import settings
from app.mcp_client import mcp_client, MCPConnectionError

logger = logging.getLogger(__name__)


def sanitize_answer(text: str) -> str:
    """Clean up common LLM formatting artifacts before showing the answer."""
    if not text:
        return text
    text = text.replace("[object Object]", "").replace("undefined", "")
    text = re.sub(r"%\s*%", "%", text)                  # collapse "% %"
    text = re.sub(r"(?<!\d)\s?%", "", text)             # strip a % not attached to a digit
    text = re.sub(r"[ \t]{2,}", " ", text)              # collapse extra spaces
    text = re.sub(r"([.,!?])\1+", r"\1", text)          # collapse duplicate punctuation
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


# =============================================================================
# System Prompt -- frames the agent's role and behavior
# =============================================================================

SYSTEM_PROMPT = """You are a media analytics assistant for a movie studio's marketing team.
You help analyze real movie metadata and audience sentiment data (from actual trailer
comments) stored in a ClickHouse database.

Your Capabilities

Query real movie metadata and audience sentiment data
Compare movies, genres, directors, and actors
Analyze relationships between movie budget, box office gross, and audience sentiment
Detect standout or unusual patterns (e.g., low budget but high positive sentiment)

Database Schema

You have access to ONE table: {database}.movie_trailers

Real movie metadata with real trailer video IDs and real audience sentiment from actual comments.

Column | Type | Description
name | String | Movie title
video_id | String | Real YouTube video ID of the trailer
rating | String | MPAA rating (PG, PG-13, R, etc.)
genre | String | Movie genre
year | Int32 | Release year
released | String | Full release date/region text
votes | Int64 | Number of audience votes (popularity signal)
director | String | Director name
writer | String | Writer name
star | String | Lead actor/star
country | String | Country of production
budget | Float64 | Production budget (USD)
gross | Float64 | Box office gross (USD)
company | String | Production company
runtime | Float32 | Movie runtime in minutes
sentiment_positive | Int32 | Count of positive audience comments on the trailer
sentiment_neutral | Int32 | Count of neutral audience comments on the trailer
sentiment_negative | Int32 | Count of negative audience comments on the trailer

Important: video_id is a real YouTube video ID. You can construct a real, working
trailer link for any row using this exact pattern:
https://www.youtube.com/watch?v={{video_id}}

This is the complete, authoritative schema for the only table you have access to.
Do not call any tool to discover tables or databases -- go straight to writing SQL
against {database}.movie_trailers.

Rules

1. ALWAYS query the database before answering. Never guess or fabricate data.

2. Be precise and concise. State the answer directly, as if you simply already knew the
   fact. Do NOT narrate your process, do not explain intermediate steps, and do NOT
   mention the database, dataset, table, or the act of querying at all. Never say things
   like "Based on the dataset", "the query returned N rows", "according to the data", or
   "1 result found" -- just state the answer.

3. Cite the actual numbers that matter from the query results in your answer.

4. Write efficient SQL -- use aggregations, filters, and sorting appropriately.

5. When comparing trailers or movies, show specific metrics side by side.

6. When detecting anomalies, define clear thresholds and explain why something is anomalous.

7. Keep simple factual answers to 1-2 plain sentences. Only use headings, bullet points, or
   tables when the question genuinely involves multiple metrics or a multi-item comparison --
   never add structure just for its own sake.

8. If the question is ambiguous, query the data and provide the most helpful interpretation,
   without asking the user to clarify first.

9. Use the {database} database when writing queries (e.g., FROM {database}.movie_trailers).

10. Never silently add a LIMIT. Only include a LIMIT clause in your SQL if the user
    explicitly asked for a specific number (e.g. "top 5", "first 10"). If the user asks for
    "all" matching rows, or gives a filter condition without a count (e.g. "list all videos
    with views more than 20M"), do NOT limit the SQL -- return every row that matches.

11. Never truncate the final answer. If your query returns N rows, your answer must
    account for all N -- either list every row (use a markdown table if there are many), or,
    if there are more than 50, state the total count and show the full table anyway. Do not
    silently show "a sample" of 5-10 rows and imply that's the complete set.

12. If you state a count of matching items, phrase it as a plain fact about the world (e.g.
    "There are 340 trailers matching this") -- never phrase it as a statement about the
    database or query itself (avoid "the query returned 340 rows", "340 rows found").

13. One query per question whenever possible. Write a single, complete SQL query that
    answers the whole question (using JOINs, subqueries, UNION, or CASE expressions as
    needed) rather than issuing several separate queries. Each additional query round-trip
    is expensive -- only issue a second query if the first result genuinely can't answer the
    question.

14. Only include a trailer link if the user explicitly asks for one -- using words like
    "link", "trailer link", "video", "URL", or "watch". If they ask a general question (e.g.
    "which movie has the best sentiment") without asking for a link, do NOT include one,
    even if it would be easy to construct. If a link IS warranted, construct it from
    video_id using the pattern https://www.youtube.com/watch?v={{video_id}} and include it
    plainly.

15. Never include SQL or raw JSON in the answer text itself -- that's shown separately via
    the SQL toggle.
"""

# =============================================================================
# Tool Declarations -- these mirror the mcp-clickhouse tools exposed to Gemini
# =============================================================================
#
# NOTE: list_tables / list_databases are intentionally NOT declared here.
# The full schema is already given in the system prompt above, so exposing a
# discovery tool just gives Gemini an extra, unnecessary round trip to call it
# on -- which doubles Gemini API usage on some questions and eats into the
# free-tier daily quota for no benefit. If the schema ever expands beyond a
# single table, re-add a discovery tool.

TOOL_DECLARATIONS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="run_query",
                description=(
                    "Execute a read-only SQL SELECT query on the ClickHouse database "
                    "containing trailer engagement data. Use this to fetch, aggregate, "
                    "filter, and analyze trailer performance metrics."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "A valid ClickHouse SQL SELECT query. Only SELECT, SHOW, "
                                "and DESCRIBE statements are allowed (read-only mode)."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]
    )
]

# Maximum iterations for the agent loop to prevent infinite tool-calling cycles
MAX_AGENT_ITERATIONS = 8


class AgentError(Exception):
    """Raised when the agent fails to produce an answer."""
    pass


class RateLimitError(AgentError):
    """Raised specifically when the Gemini API rate/quota limit is hit."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)")


class TrailerAgent:
    """
    Orchestrates the question-answering flow between Gemini and ClickHouse.

    For each question:
        1. Builds a conversation with the system prompt and user question
        2. Calls Gemini with tool declarations
        3. If Gemini requests a function call, executes it via MCP
        4. Feeds the result back to Gemini
        5. Repeats until a final text answer is produced
        6. Returns the answer along with reasoning steps and SQL queries used
    """

    def __init__(self):
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        # Format the system prompt with the configured database name
        self._system_prompt = SYSTEM_PROMPT.format(
            database=settings.clickhouse_database,
        )

    async def process_question(self, question: str) -> dict:
        """
        Process a natural-language question and return the agent's answer.

        Args:
            question: The user's question about trailer performance.

        Returns:
            Dict with keys: 'answer', 'reasoning_steps', 'sql_used'

        Raises:
            AgentError: If the agent cannot produce an answer.
            RateLimitError: If the Gemini API quota/rate limit is hit.
            MCPConnectionError: If ClickHouse queries fail via MCP.
        """
        reasoning_steps: list[str] = []
        sql_queries: list[str] = []

        # Build the initial conversation
        contents: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=question)],
            ),
        ]

        # Configure Gemini to use manual function calling
        generate_config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            tools=TOOL_DECLARATIONS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
            ),
            temperature=0.1,  # Low temperature for analytical precision
        )

        # =====================================================================
        # Agent loop: call Gemini -> execute tool -> feed result -> repeat
        # =====================================================================
        for iteration in range(MAX_AGENT_ITERATIONS):
            logger.info("Agent iteration %d/%d", iteration + 1, MAX_AGENT_ITERATIONS)

            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=generate_config,
                )
            except Exception as e:
                msg = str(e)
                if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                    match = _RETRY_DELAY_RE.search(msg)
                    retry_after = float(match.group(1)) if match else None
                    wait_str = f"about {int(retry_after)}s" if retry_after else "about a minute"
                    logger.error("Gemini rate/quota limit hit: %s", msg)
                    raise RateLimitError(
                        f"The Gemini API rate limit was hit -- this is a free-tier daily "
                        f"quota, not a bug. Try again in {wait_str}, or enable billing on "
                        f"the Google Cloud project to raise the limit.",
                        retry_after=retry_after,
                    ) from e

                logger.error("Gemini API call failed: %s", msg)
                raise AgentError(f"Failed to get response from Gemini: {msg}") from e

            # Check if the response contains function calls
            if response.function_calls:
                # Process each function call from Gemini
                function_response_parts = []

                for func_call in response.function_calls:
                    tool_name = func_call.name
                    tool_args = func_call.args or {}

                    logger.info(
                        "Gemini requested tool call: %s(%s)",
                        tool_name,
                        tool_args,
                    )

                    # Track the SQL query if it's a run_query call
                    if tool_name == "run_query" and "query" in tool_args:
                        sql_queries.append(tool_args["query"])
                        reasoning_steps.append(f"Executing SQL: {tool_args['query']}")

                    # Execute the tool call via MCP
                    try:
                        result = await self._execute_tool(tool_name, tool_args)
                        reasoning_steps.append(f"Tool '{tool_name}' returned data successfully")
                    except MCPConnectionError as e:
                        result = f"Error executing query: {str(e)}"
                        reasoning_steps.append(f"Tool '{tool_name}' failed: {str(e)}")

                    # Build a FunctionResponse part
                    function_response_parts.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": result},
                        )
                    )

                # Add the model's function call to the conversation
                contents.append(
                    types.Content(
                        role="model",
                        parts=response.candidates[0].content.parts,
                    )
                )

                # Add the function response(s) to the conversation
                contents.append(
                    types.Content(
                        role="user",
                        parts=function_response_parts,
                    )
                )

            else:
                # No function calls -- Gemini produced a final text answer
                answer_text = sanitize_answer(response.text) or "No answer generated."
                reasoning_steps.append("Generated final answer")

                return {
                    "answer": answer_text,
                    "reasoning_steps": reasoning_steps,
                    "sql_used": "\n\n".join(sql_queries) if sql_queries else None,
                }

        # If we exhausted all iterations without a final answer
        raise AgentError(
            f"Agent did not converge after {MAX_AGENT_ITERATIONS} iterations. "
            "The question may be too complex."
        )

    async def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Route a Gemini tool call to the appropriate MCP client method.

        Args:
            tool_name: The tool to execute ('run_query', 'list_tables', etc.)
            arguments: Arguments dict from Gemini's function_call.

        Returns:
            Text result from the MCP tool.

        Raises:
            MCPConnectionError: If the MCP call fails.
            AgentError: If the tool name is unknown.
        """
        if tool_name == "run_query":
            query = arguments.get("query", "")
            return await mcp_client.execute_query(query)

        elif tool_name == "list_tables":
            database = arguments.get("database", settings.clickhouse_database)
            return await mcp_client.list_tables(database)

        elif tool_name == "list_databases":
            return await mcp_client.list_databases()

        else:
            raise AgentError(f"Unknown tool requested by model: {tool_name}")


# Singleton instance
agent = TrailerAgent()
