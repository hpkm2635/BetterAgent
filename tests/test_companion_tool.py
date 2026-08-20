import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.cognitive.tools.companion_tool import (
    AddScheduleTool,
    QueryScheduleTool,
    DeleteScheduleTool,
    QueryCompanionStatsTool,
)
from services.cognitive.tool_registry import ToolRegistry


def test_tool_registry_contains_companion_tools():
    registry = ToolRegistry()
    schemas = registry.get_all_schemas()
    names = [s["name"] for s in schemas]

    assert "add_schedule" in names
    assert "query_schedule" in names
    assert "delete_schedule" in names
    assert "query_companion_stats" in names


@pytest.mark.asyncio
async def test_add_schedule_tool_execution():
    tool = AddScheduleTool()
    assert tool.name == "add_schedule"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"schedule_id": "test-uuid-123", "status": "scheduled"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await tool.execute(title="测试期末考试", remind_at="2026-08-25 09:00:00")
        assert res["status"] == "success"
        assert res["schedule_id"] == "test-uuid-123"


@pytest.mark.asyncio
async def test_query_companion_stats_tool_execution():
    tool = QueryCompanionStatsTool()
    assert tool.name == "query_companion_stats"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answer": "这周你一共和我聊了 15 次喵～",
        "sql_executed": "SELECT SUM(msg_count) FROM chat_stats",
        "raw_result": [{"total": 15}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await tool.execute(query="这周我们聊了多少次？")
        assert res["status"] == "success"
        assert "15" in res["answer"]
