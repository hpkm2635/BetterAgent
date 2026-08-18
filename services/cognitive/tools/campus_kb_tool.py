import os
import logging
from typing import Dict, Any, Optional, List
import httpx
from services.cognitive.tools.base_tool import BaseTool
from shared.config_loader import get_config_val

logger = logging.getLogger("campus_kb_tool")


class CampusKBTool(BaseTool):
    """
    Cognitive tool for querying the Campus Knowledge Base (services/campus_kb) RAG service.
    Enables autonomous Tool Calling for LLM reasoning models (Qwen, Gemini, Claude, OpenAI).
    """

    @property
    def name(self) -> str:
        return "search_campus_kb"

    @property
    def description(self) -> str:
        return (
            "查询校园知识库、校规规章、图书馆时间、食堂安排、选课指南、校园设施等相关信息。"
            "当用户询问校园生活、校规规章、学校设施或知识库相关服务时主动调用此工具。"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或具体问题，例如：'图书馆开门时间' 或 '选课规定'。"
                },
                "category": {
                    "type": "string",
                    "description": "可选的分类过滤（如 'library', 'canteen', 'exam', 'facility'）。"
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回的结果条数上限，默认 3 条。"
                }
            },
            "required": ["query"]
        }

    async def execute(self, query: str, category: Optional[str] = None, top_k: int = 3, **kwargs) -> Dict[str, Any]:
        if not query or not query.strip():
            return {
                "status": "failed",
                "error": "Query parameter cannot be empty.",
                "facts": []
            }

        kb_url = get_config_val("infrastructure.campus_kb_url", os.getenv("CAMPUS_KB_URL", "http://127.0.0.1:8093"))
        endpoint = f"{kb_url.rstrip('/')}/api/kb/search"
        payload = {
            "query": query.strip(),
            "top_k": max(1, min(top_k, 10)),
        }
        if category:
            payload["category"] = category

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(endpoint, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    facts: List[Dict[str, Any]] = []
                    for item in results:
                        content = item.get("content", "")
                        source = item.get("source", "")
                        score = item.get("score", 0.0)
                        if content:
                            facts.append({
                                "content": content,
                                "source": source,
                                "relevance_score": score
                            })

                    return {
                        "status": "success",
                        "query": query,
                        "total_found": len(facts),
                        "facts": facts,
                        "message": f"成功在校园知识库查找到 {len(facts)} 条相关知识。" if facts else "校园知识库未找到匹配记录。"
                    }
                else:
                    logger.warning(f"Campus KB service returned non-200 status code: {resp.status_code}")
                    return {
                        "status": "failed",
                        "error": f"Campus KB service HTTP {resp.status_code}",
                        "facts": []
                    }
        except Exception as e:
            logger.warning(f"Failed to query Campus KB service at {endpoint}: {e}")
            return {
                "status": "failed",
                "error": f"Campus KB service connection failed ({e}).",
                "facts": []
            }
