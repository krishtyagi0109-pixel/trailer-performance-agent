"""Quick test script for MCP health check debugging."""
"""Quick test script for MCP tool discovery + health check debugging."""
import asyncio
import traceback
from app.mcp_client import mcp_client, MCPConnectionError


async def list_tools():
    """Directly list available tools from the mcp-clickhouse server."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = mcp_client._get_server_params()

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("=== AVAILABLE TOOLS ===")
            for tool in tools.tools:
                print(f"- {tool.name}: {tool.description}")


async def test():
    print("Listing tools first...")
    try:
        await list_tools()
    except Exception as e:
        print(f"Tool listing failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return

    print("\nTesting MCP health check...")
    try:
        result = await mcp_client.execute_query("SELECT 1 AS health_check")
        print(f"SUCCESS: {result}")
    except MCPConnectionError as e:
        print(f"MCPConnectionError: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test())
