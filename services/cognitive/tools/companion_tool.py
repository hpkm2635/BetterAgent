import os
import logging
from typing import Dict, Any, Optional
import httpx
from services.cognitive.tools.base_tool import BaseTool
from shared.config_loader import get_config_val

logger = logging.getLogger("companion_tool")


class AddScheduleTool(BaseTool):
    """
    Cognitive tool for adding a schedule reminder via Companion service (services/companion :8096).
    """

    @property
    def name(self) -> str:
        return "add_schedule"

    @property
    def description(self) -> str:
        return (
            "为用户新增一个校园/日常定时提醒事项。"
            "当用户要求记录提醒、考试安排、图书归还或定时任务时主动调用此工具。"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "提醒事项的标题或名称，例如：'高数期末考试' 或 '归还图书'。"
                },
                "remind_at": {
                    "type": "string",
                    "description": "提醒发生的时间点，格式为 ISO 8601 或 'YYYY-MM-DD HH:MM:SS'。"
                },
                "note": {
                    "type": "string",
                    "description": "可选的备注或说明。"
                },
                "chat_id": {
                    "type": "integer",
                    "description": "当前对话 ID，默认 1001。"
                }
            },
            "required": ["title", "remind_at"]
        }

    async def execute(self, title: str, remind_at: str, note: str = "", chat_id: int = 1001, **kwargs) -> Dict[str, Any]:
        if not title or not title.strip():
            return {"status": "failed", "error": "Title cannot be empty."}
        if not remind_at or not remind_at.strip():
            return {"status": "failed", "error": "Remind time cannot be empty."}

        companion_url = get_config_val("infrastructure.companion_url", os.getenv("COMPANION_URL", "http://127.0.0.1:8096"))
        endpoint = f"{companion_url.rstrip('/')}/api/schedule/add"
        payload = {
            "chat_id": chat_id,
            "user_id": 1,
            "title": title.strip(),
            "remind_at": remind_at.strip(),
            "note": note.strip() if note else "",
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(endpoint, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "status": "success",
                        "schedule_id": data.get("schedule_id", ""),
                        "title": title.strip(),
                        "remind_at": remind_at.strip(),
                        "message": f"成功为主人创建提醒事项：'{title}'（时间：{remind_at}）喵～"
                    }
                else:
                    return {"status": "failed", "error": f"Companion HTTP {resp.status_code}"}
        except Exception as e:
            logger.warning(f"Failed to call companion add_schedule at {endpoint}: {e}")
            return {"status": "failed", "error": f"Companion connection failed ({e})."}


class QueryScheduleTool(BaseTool):
    """
    Cognitive tool for querying active schedules from Companion service.
    """

    @property
    def name(self) -> str:
        return "query_schedule"

    @property
    def description(self) -> str:
        return "查询用户当前所有已设置的日程与到期提醒列表。"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "integer",
                    "description": "当前对话 ID，默认 1001。"
                }
            }
        }

    async def execute(self, chat_id: int = 1001, **kwargs) -> Dict[str, Any]:
        companion_url = get_config_val("infrastructure.companion_url", os.getenv("COMPANION_URL", "http://127.0.0.1:8096"))
        endpoint = f"{companion_url.rstrip('/')}/api/schedule/list?chat_id={chat_id}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(endpoint)
                if resp.status_code == 200:
                    schedules = resp.json().get("schedules", [])
                    return {
                        "status": "success",
                        "total": len(schedules),
                        "schedules": schedules,
                        "message": f"查询到 {len(schedules)} 条待办日程喵～" if schedules else "目前没有待办日程喵～"
                    }
                else:
                    return {"status": "failed", "error": f"Companion HTTP {resp.status_code}"}
        except Exception as e:
            logger.warning(f"Failed to query schedules at {endpoint}: {e}")
            return {"status": "failed", "error": f"Companion connection failed ({e})."}


class DeleteScheduleTool(BaseTool):
    """
    Cognitive tool for deleting a schedule item by schedule_id.
    """

    @property
    def name(self) -> str:
        return "delete_schedule"

    @property
    def description(self) -> str:
        return "删除特定的日程提醒项。需要提供 schedule_id。"

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "string",
                    "description": "要删除的日程唯一 ID。"
                }
            },
            "required": ["schedule_id"]
        }

    async def execute(self, schedule_id: str, **kwargs) -> Dict[str, Any]:
        if not schedule_id:
            return {"status": "failed", "error": "schedule_id is required."}

        companion_url = get_config_val("infrastructure.companion_url", os.getenv("COMPANION_URL", "http://127.0.0.1:8096"))
        endpoint = f"{companion_url.rstrip('/')}/api/schedule/{schedule_id}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.delete(endpoint)
                if resp.status_code == 200:
                    return {"status": "success", "message": f"成功删除日程 {schedule_id} 喵～"}
                else:
                    return {"status": "failed", "error": f"Schedule {schedule_id} not found."}
        except Exception as e:
            logger.warning(f"Failed to delete schedule {schedule_id}: {e}")
            return {"status": "failed", "error": f"Companion connection failed ({e})."}


class QueryCompanionStatsTool(BaseTool):
    """
    Cognitive tool for querying chat statistics and mood history via NL2SQL in Companion service.
    """

    @property
    def name(self) -> str:
        return "query_companion_stats"

    @property
    def description(self) -> str:
        return (
            "用自然语言查询对话统计与情绪历史（如：'我们这周聊了多少次'，'今天聊了多少句'，'我最近的心情怎样'）。"
            "当用户询问陪伴历史、对话统计或情绪数据时调用此工具。"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然语言问题，例如：'这周我们聊了多少次？'。"
                },
                "chat_id": {
                    "type": "integer",
                    "description": "当前对话 ID，默认 1001。"
                }
            },
            "required": ["query"]
        }

    async def execute(self, query: str, chat_id: int = 1001, **kwargs) -> Dict[str, Any]:
        if not query or not query.strip():
            return {"status": "failed", "error": "Query cannot be empty."}

        companion_url = get_config_val("infrastructure.companion_url", os.getenv("COMPANION_URL", "http://127.0.0.1:8096"))
        endpoint = f"{companion_url.rstrip('/')}/api/companion/query"
        payload = {"chat_id": chat_id, "natural_language_query": query.strip()}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(endpoint, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "status": "success",
                        "answer": data.get("answer", ""),
                        "sql_executed": data.get("sql_executed", ""),
                        "raw_result": data.get("raw_result", []),
                    }
                else:
                    return {"status": "failed", "error": f"Companion query failed ({resp.status_code})."}
        except Exception as e:
            logger.warning(f"Failed to query companion stats at {endpoint}: {e}")
            return {"status": "failed", "error": f"Companion connection failed ({e})."}
