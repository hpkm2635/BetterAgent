import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.cognitive.mcp.client import McpSession

logger = logging.getLogger("presenter_manager")


class PresenterSessionManager:
    """
    Owns MCP presenter sessions (PPT / VSCode) scoped per chat_id.

    activate() is the *only* path that makes ppt_*/vscode_* tools visible to
    a given chat_id at all (see PresenterControlTool + CognitiveEngine.
    stream_reasoning_loop, which merges get_active_tool_schemas() into
    tools_schema only for chats with an active session). Outside of that,
    the LLM has no way to even discover these tools exist.

    Idle sessions are swept so a forgotten "deactivate" doesn't leave
    PowerPoint / a VSCode workspace session hanging around forever.
    """

    def __init__(self, server_commands: Dict[str, List[str]], idle_timeout_seconds: float = 600.0):
        self._server_commands = server_commands
        self._idle_timeout = idle_timeout_seconds
        self._sessions: Dict[int, Dict[str, McpSession]] = {}
        self._last_used: Dict[int, Dict[str, float]] = {}

    async def activate(self, chat_id: int, target: str, root_path: Optional[str] = None) -> str:
        command = self._server_commands.get(target)
        if not command:
            return f"未知或未配置的 presenter 目标: {target}"

        chat_sessions = self._sessions.setdefault(chat_id, {})
        existing = chat_sessions.get(target)
        if existing is not None:
            new_root = str(Path(root_path).resolve()) if root_path else None
            old_root = None
            if "--root" in existing._command:
                idx = existing._command.index("--root")
                if idx + 1 < len(existing._command):
                    old_root = str(Path(existing._command[idx + 1]).resolve())

            if new_root and old_root and new_root != old_root:
                logger.info(f"Re-activating {target} session with updated root_path: {new_root} (was {old_root})")
                await self.deactivate(chat_id, target)
            else:
                self._touch(chat_id, target)
                return f"{target} 已经是激活状态"

        # root_path confines the server's file-facing tools (vscode_read_range,
        # ppt_open, ...) to a single directory -- without it those servers
        # fail closed rather than accepting an arbitrary model-supplied path.
        spawn_command = list(command)
        if root_path:
            spawn_command += ["--root", root_path]

        session = McpSession(spawn_command)
        try:
            await session.start()
        except Exception as e:
            logger.error(f"Failed to start MCP session for target={target}, chat_id={chat_id}: {e}")
            return f"启动 {target} MCP server 失败: {e}"

        chat_sessions[target] = session
        self._touch(chat_id, target)
        tool_names = ", ".join(t.name for t in session.tools) or "(no tools)"
        logger.info(f"Presenter session activated: chat_id={chat_id} target={target} tools=[{tool_names}]")
        return f"{target} 已激活，可用工具: {tool_names}"

    async def deactivate(self, chat_id: int, target: Optional[str] = None) -> str:
        chat_sessions = self._sessions.get(chat_id)
        if not chat_sessions:
            return "没有正在运行的 presenter 会话"

        targets = [target] if target else list(chat_sessions.keys())
        closed = []
        for t in targets:
            session = chat_sessions.pop(t, None)
            if session is not None:
                await session.close()
                closed.append(t)
            self._last_used.get(chat_id, {}).pop(t, None)

        if not chat_sessions:
            self._sessions.pop(chat_id, None)
            self._last_used.pop(chat_id, None)

        return f"已关闭: {', '.join(closed)}" if closed else "没有匹配的会话可关闭"

    def _touch(self, chat_id: int, target: str) -> None:
        self._last_used.setdefault(chat_id, {})[target] = time.time()

    def get_active_tool_schemas(self, chat_id: int) -> List[Dict[str, Any]]:
        chat_sessions = self._sessions.get(chat_id)
        if not chat_sessions:
            return []
        schemas: List[Dict[str, Any]] = []
        for session in chat_sessions.values():
            schemas.extend(session.get_tool_schemas())
        return schemas

    def _find_session(self, chat_id: int, tool_name: str) -> Tuple[Optional[str], Optional[McpSession]]:
        for target, session in self._sessions.get(chat_id, {}).items():
            if session.has_tool(tool_name):
                return target, session
        return None, None

    async def call_tool(self, chat_id: int, tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        target, session = self._find_session(chat_id, tool_name)
        if session is None:
            return None

        self._touch(chat_id, target)
        try:
            return await session.call_tool(tool_name, args)
        except Exception as e:
            logger.error(f"MCP tool call failed: chat_id={chat_id} tool={tool_name}: {e}")
            return {"error": True, "detail": str(e)}

    async def sweep_idle(self) -> None:
        now = time.time()
        for chat_id in list(self._last_used.keys()):
            for target, ts in list(self._last_used.get(chat_id, {}).items()):
                if now - ts > self._idle_timeout:
                    logger.info(f"Presenter session idle-timeout: chat_id={chat_id} target={target}")
                    await self.deactivate(chat_id, target)
