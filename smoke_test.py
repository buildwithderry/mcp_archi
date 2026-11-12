# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0,<2"]
# ///
"""Smoke test: spawn the server over stdio, list tools, call list_models."""
import asyncio, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="uv", args=["run", "--script", str(Path(__file__).with_name("archi_mcp.py"))])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(f"{len(tools.tools)} tools")
            res = await s.call_tool("list_models", {})
            print(res.content[0].text[:300])
asyncio.run(main())
