import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from services.cognitive.tools.campus_kb_tool import CampusKBTool
from services.cognitive.tool_registry import ToolRegistry
from services.cognitive.providers.factory import ProviderFactory


def test_campus_kb_tool_schema():
    tool = CampusKBTool()
    assert tool.name == "search_campus_kb"
    assert "校园知识库" in tool.description
    schema = tool.parameters_schema
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
    assert "query" in schema["required"]


@pytest.mark.asyncio
async def test_campus_kb_tool_execute_success():
    tool = CampusKBTool()
    mock_response_data = {
        "results": [
            {"content": "图书馆周一至周日 08:00 - 22:00 开放", "source": "library_faq.txt", "score": 0.92}
        ],
        "query": "图书馆时间",
        "total": 1
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_response_data

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        res = await tool.execute(query="图书馆时间")

        assert res["status"] == "success"
        assert res["total_found"] == 1
        assert len(res["facts"]) == 1
        assert "08:00 - 22:00" in res["facts"][0]["content"]


@pytest.mark.asyncio
async def test_campus_kb_tool_execute_offline_fallback():
    tool = CampusKBTool()
    with patch("httpx.AsyncClient.post", side_effect=Exception("Connection refused")):
        res = await tool.execute(query="测试崩溃降级")
        assert res["status"] == "failed"
        assert res["facts"] == []
        assert "connection failed" in res["error"]


def test_tool_registry_includes_campus_kb():
    registry = ToolRegistry()
    assert registry.get_tool("search_campus_kb") is not None
    schemas = registry.get_all_schemas()
    names = [s["name"] for s in schemas]
    assert "search_campus_kb" in names


def test_qwen_provider_includes_campus_kb_in_built_tools():
    provider = ProviderFactory.get_provider("qwen")
    registry = ToolRegistry()
    schemas = registry.get_all_schemas()
    built_tools = provider._build_tools(schemas)
    tool_names = [t["function"]["name"] for t in built_tools]
    assert "search_campus_kb" in tool_names
