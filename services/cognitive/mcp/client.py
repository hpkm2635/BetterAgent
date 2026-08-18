import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("mcp_client")


class McpToolSpec:
    __slots__ = ("name", "description", "input_schema")

    def __init__(self, name: str, description: str, input_schema: Dict[str, Any]):
        self.name = name
        self.description = description
        self.input_schema = input_schema


class McpSession:
    """
    Owns one MCP server child process (stdio transport) for as long as a
    presenter mode stays activated. Not shared across chat_ids -- see
    PresenterSessionManager, which keeps one McpSession per (chat_id, target).

    Stdio transport means the child's pipes are only reachable by whichever
    process spawned it (us), so unlike a network-listening MCP server this
    needs no separate token auth -- process isolation is the boundary.
    """

    def __init__(self, command: List[str]):
        self._command = command
        self._stdio_ctx = None
        self._session_ctx = None
        self._session: Optional[ClientSession] = None
        self.tools: List[McpToolSpec] = []

    async def start(self) -> None:
        if self._session is not None:
            return
        if not self._command:
            raise ValueError("MCP server command is empty")

        try:
            params = StdioServerParameters(command=self._command[0], args=list(self._command[1:]))
            self._stdio_ctx = stdio_client(params)
            read, write = await self._stdio_ctx.__aenter__()

            self._session_ctx = ClientSession(read, write)
            self._session = await self._session_ctx.__aenter__()

            # NOTICE: PPT/VSCode may be slow to cold-start; without a timeout a
            # hung subprocess silently starves the reasoning loop for this chat.
            await asyncio.wait_for(self._session.initialize(), timeout=15.0)
            listed = await asyncio.wait_for(self._session.list_tools(), timeout=10.0)

            self.tools = [
                McpToolSpec(t.name, t.description or "", t.inputSchema or {"type": "object", "properties": {}})
                for t in listed.tools
            ]
            logger.info(f"MCP session started ({' '.join(self._command)}): {len(self.tools)} tool(s) discovered")
        except asyncio.TimeoutError:
            logger.error(f"MCP session start timed out for command: {self._command}")
            await self.close()
            raise RuntimeError(f"MCP server did not respond in time: {self._command[0]}")
        except Exception:
            await self.close()
            raise

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in self.tools
        ]

    def has_tool(self, name: str) -> bool:
        return any(t.name == name for t in self.tools)

    async def call_tool(self, name: str, args: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        if self._session is None:
            raise RuntimeError("MCP session not started")

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, arguments=args or {}),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"MCP call_tool timed out ({name}) after {timeout}s")
            raise RuntimeError(f"MCP tool execution timed out: {name}")

        payload = getattr(result, "structuredContent", None)
        if payload is None:
            text_parts = [
                block.text for block in (result.content or [])
                if getattr(block, "type", None) == "text"
            ]
            joined = "\n".join(text_parts)
            try:
                payload = json.loads(joined) if joined else {}
            except (json.JSONDecodeError, TypeError):
                payload = {"text": joined}

        if getattr(result, "isError", False):
            payload = {"error": True, "detail": payload}

        return payload

    async def close(self) -> None:
        # NOTICE: manual __aenter__/__aexit__ instead of `async with` because
        # the session needs to outlive a single call -- it's kept alive across
        # many call_tool() invocations for as long as presenter mode is on.
        # A hard process kill (SIGKILL / crash) still orphans the child; only
        # a clean shutdown path reaches this. Not covered by this change.
        for ctx in (self._session_ctx, self._stdio_ctx):
            if ctx is None:
                continue
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing MCP session/transport: {e}")

        self._session = None
        self._session_ctx = None
        self._stdio_ctx = None
        self.tools = []
