import asyncio
import logging
import os
import shutil
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import settings

logger = logging.getLogger(__name__)


class MCPConnectionError(Exception):
    pass


class MCPClickHouseClient:
    def __init__(self):
        # Defaults below are overridable by anything already set in os.environ
        # or settings.get_clickhouse_env(). CLICKHOUSE_MCP_QUERY_TIMEOUT controls
        # the mcp-clickhouse *server's own* internal query timeout (default "30")
        # — separate from, and more important than, the client-side timeout on
        # our _call_tool wrapper below. ClickHouse Cloud free-tier services can
        # be idle-suspended and take 15-30s to wake from a cold start, so a
        # 30s server-side cap can get eaten entirely by the wake-up itself.
        self._env = {
            "CLICKHOUSE_MCP_QUERY_TIMEOUT": "90",
            "CLICKHOUSE_CONNECT_TIMEOUT": "30",
            "CLICKHOUSE_SEND_RECEIVE_TIMEOUT": "90",
            **os.environ,
            **settings.get_clickhouse_env(),
        }
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

        uv_path = shutil.which("uv")
        if uv_path:
            self._command = uv_path
            self._args = ["run", "--with", "mcp-clickhouse", "--python", python_version, "mcp-clickhouse"]
        else:
            self._command = shutil.which("mcp-clickhouse") or "mcp-clickhouse"
            self._args = []

        self._session = None
        self._session_cm = None
        self._stdio_cm = None
        self._lock = asyncio.Lock()

    def _get_server_params(self) -> StdioServerParameters:
        return StdioServerParameters(command=self._command, args=self._args, env=self._env)

    async def _open_session(self):
        """Spawn the MCP server subprocess and open a session against it."""
        server_params = self._get_server_params()
        self._stdio_cm = stdio_client(server_params)
        read_stream, write_stream = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()

        # IMPORTANT: initialize() has no built-in timeout. If the
        # mcp-clickhouse subprocess starts but never completes the MCP
        # handshake (bad env var, polluted stdout, etc.) this call would
        # otherwise hang forever — which blocks the FastAPI lifespan,
        # which means Uvicorn never binds its port, which is what was
        # causing Render's "Port scan timeout reached" failure. Wrapping
        # it here turns a silent infinite hang into a clear, fast error.
        try:
            await asyncio.wait_for(self._session.initialize(), timeout=30.0)
        except asyncio.TimeoutError:
            raise MCPConnectionError(
                "Timed out waiting for the mcp-clickhouse subprocess to complete "
                "its MCP handshake (30s). It likely started but never responded — "
                "check that CLICKHOUSE_HOST/PORT/SECURE are correct and that the "
                "subprocess isn't blocked or writing non-protocol output to stdout."
            )

    async def connect(self):
        """Call once at app startup to open a persistent MCP session."""
        await self._open_session()
        logger.info("Persistent MCP session established")

        # Warm up the ClickHouse Cloud connection, with retries. Free-tier
        # services can be idle-suspended and take 15-30s to wake on the first
        # query after inactivity — a single attempt can land squarely inside
        # that window and fail (SSL/connection errors, timeouts). Retry a
        # few times with backoff here, at startup, instead of making the
        # user's first real question eat the cold-start latency or fail.
        #
        # NOTE: each attempt uses a shorter, startup-specific timeout
        # (20s) rather than the default 100s used for normal queries.
        # Without this, a single stuck attempt could eat up to 100s —
        # well past Render's port-scan window — before the retry loop
        # even gets a chance to try again.
        for attempt in range(1, 4):
            try:
                await self.execute_query("SELECT 1", timeout=20.0)
                logger.info("ClickHouse warm-up query succeeded (attempt %d/3)", attempt)
                break
            except Exception as e:
                logger.warning(
                    "ClickHouse warm-up attempt %d/3 failed (service may be "
                    "cold-starting): %s", attempt, e
                )
                if attempt < 3:
                    await asyncio.sleep(5 * attempt)

    async def _reconnect(self):
        """
        Tear down and re-establish the persistent MCP session. Used when a
        tool call fails with a connection-level error (dropped SSL socket,
        broken pipe, reset) — the underlying ClickHouse connection inside the
        mcp-clickhouse subprocess can go stale after a network blip, and the
        cleanest fix is a fresh session rather than trying to nurse the old
        one back to life.
        """
        try:
            if self._session_cm:
                await self._session_cm.__aexit__(None, None, None)
            if self._stdio_cm:
                await self._stdio_cm.__aexit__(None, None, None)
        except Exception as e:
            logger.warning("Error tearing down stale MCP session (continuing anyway): %s", e)
        finally:
            self._session = None
            self._session_cm = None
            self._stdio_cm = None

        await self._open_session()
        logger.info("MCP session reconnected after a connection-level failure")

    async def disconnect(self):
        """Call once at app shutdown."""
        if self._session_cm:
            await self._session_cm.__aexit__(None, None, None)
        if self._stdio_cm:
            await self._stdio_cm.__aexit__(None, None, None)

    # Markers of a one-off connection-level failure (dropped SSL socket,
    # reset, broken pipe) as opposed to a real query error (bad SQL, missing
    # table). These are worth one automatic reconnect + retry.
    _CONNECTION_DROP_MARKERS = (
        "SSLEOFError",
        "SSLError",
        "EOF occurred",
        "Connection reset",
        "Connection aborted",
        "BrokenPipeError",
        "ConnectionResetError",
        "RemoteDisconnected",
        "Server disconnected",
    )

    def _is_connection_drop(self, msg: str) -> bool:
        return any(marker.lower() in msg.lower() for marker in self._CONNECTION_DROP_MARKERS)

    async def _call_tool(self, tool_name: str, arguments: dict, timeout: float = 100.0) -> str:
        if not self._session:
            raise MCPConnectionError("MCP session not initialized — call connect() first")

        attempted_reconnect = False

        while True:
            async with self._lock:  # MCP stdio sessions handle one call at a time
                try:
                    result = await asyncio.wait_for(
                        self._session.call_tool(tool_name, arguments=arguments),
                        timeout=timeout,
                    )
                    break
                except asyncio.TimeoutError:
                    raise MCPConnectionError(
                        f"MCP tool '{tool_name}' timed out after {timeout}s. "
                        "If this is the first query in a while, ClickHouse Cloud "
                        "may have been idle-suspended and is waking up — try again."
                    )
                except Exception as e:
                    should_retry = (not attempted_reconnect) and self._is_connection_drop(str(e))
                    if not should_retry:
                        raise MCPConnectionError(f"MCP tool '{tool_name}' failed: {e}") from e
                    # fall through: lock releases here, reconnect happens below

            # Only reached when the call above hit a retryable connection drop
            attempted_reconnect = True
            logger.warning(
                "MCP tool '%s' hit a connection-level error, reconnecting once before retrying",
                tool_name,
            )
            try:
                await self._reconnect()
            except Exception as reconnect_err:
                raise MCPConnectionError(
                    f"MCP tool '{tool_name}' failed and the reconnect attempt also failed: {reconnect_err}"
                ) from None

        if result.content:
            text_parts = [b.text for b in result.content if hasattr(b, "text")]
            return "\n".join(text_parts) if text_parts else str(result.content)
        return "No results returned."

    async def execute_query(self, sql: str, timeout: float = 100.0) -> str:
        return await self._call_tool("run_query", {"query": sql}, timeout=timeout)

    async def list_databases(self) -> str:
        return await self._call_tool("list_databases", {})

    async def list_tables(self, database: str) -> str:
        return await self._call_tool("list_tables", {"database": database})

    async def health_check(self) -> bool:
        try:
            result = await self.execute_query("SELECT 1 AS health_check")
            return bool(result)
        except Exception:
            return False


mcp_client = MCPClickHouseClient()
