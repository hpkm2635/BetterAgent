"""
API Contract Integration Tests — BetterAgent
============================================
运行方式:
  pytest tests/test_api_contract.py -v
  pytest tests/test_api_contract.py -v -k TestCampusKB
  pytest tests/test_api_contract.py -v -k TestAdminPanel
  pytest tests/test_api_contract.py -v -k TestCompanionTools

前提: 被测服务已在对应端口启动
  Campus KB    → http://localhost:8093
  Admin Panel  → http://localhost:8094
  Companion    → http://localhost:8096

作者: 技术总监（维护）
"""
import pytest
import requests

KB_BASE    = "http://localhost:8093"
ADMIN_BASE = "http://localhost:8094"
COMP_BASE  = "http://localhost:8096"

# ─── 校园知识库 ───────────────────────────────────────────────────────────────

class TestCampusKB:
    """任务2 / feature: feat/campus-kb / 负责人: 零只蚊子"""

    def test_health(self):
        r = requests.get(f"{KB_BASE}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_ingest_three_docs(self):
        docs = [
            {"content": "图书馆周一至周五开放至22:00，周末20:00关闭。",
             "source": "test_lib.md", "category": "faq", "metadata": {}},
            {"content": "校内超市位于第三食堂一楼，营业时间07:00-23:00。",
             "source": "test_shop.md", "category": "service", "metadata": {}},
            {"content": "选课系统每学期第9周开放，登录教务处网站操作。",
             "source": "test_schedule.md", "category": "schedule", "metadata": {}},
        ]
        r = requests.post(f"{KB_BASE}/api/kb/ingest", json={"documents": docs}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ingested"] == 3
        assert body["failed"] == 0

    def test_search_returns_result(self):
        r = requests.post(f"{KB_BASE}/api/kb/search",
                          json={"query": "图书馆几点关门", "top_k": 3}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 1
        result = body["results"][0]
        assert "content" in result
        assert "score" in result
        assert "source" in result

    def test_search_response_shape(self):
        r = requests.post(f"{KB_BASE}/api/kb/search",
                          json={"query": "选课", "top_k": 5}, timeout=10)
        assert r.status_code == 200
        body = r.json()
        # 必须包含这三个顶级字段
        assert "results" in body
        assert "query" in body
        assert "total" in body
        assert isinstance(body["results"], list)

    def test_search_no_crash_on_empty_match(self):
        r = requests.post(f"{KB_BASE}/api/kb/search",
                          json={"query": "xyzzy不存在abc", "top_k": 3}, timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert "results" in body  # 不崩溃，返回空列表即可

    def test_ingest_bad_request_graceful(self):
        """缺少必填字段时，应返回 4xx 而非 500"""
        r = requests.post(f"{KB_BASE}/api/kb/ingest",
                          json={"documents": [{"no_content": True}]}, timeout=10)
        assert r.status_code in (200, 400, 422)  # 不能 5xx

# ─── 后台管理系统 ─────────────────────────────────────────────────────────────

class TestAdminPanel:
    """任务5 / feature: feat/admin-panel / 负责人: 谢自立"""

    def test_health(self):
        r = requests.get(f"{ADMIN_BASE}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_list_personas_contains_both(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas", timeout=5)
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()["personas"]]
        assert "catgirl" in ids
        assert "patra" in ids

    def test_get_persona_detail_shape(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "catgirl"
        assert "name" in body
        assert "base_prompt" in body

    def test_get_persona_not_found(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas/not_exist_xyz", timeout=5)
        assert r.status_code == 404

    def test_patch_persona_allowed_field(self):
        # 备份原始 name
        original = requests.get(
            f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5).json()["name"]
        # 修改
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"name": "__contract_test__"}, timeout=5)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"
        # 验证实际写入
        updated = requests.get(
            f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5).json()["name"]
        assert updated == "__contract_test__"
        # 恢复
        requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                       json={"name": original}, timeout=5)

    def test_patch_persona_forbidden_tts(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"tts": {"provider": "evil"}}, timeout=5)
        assert r.status_code == 400
        assert "error" in r.json()

    def test_patch_persona_forbidden_id(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"id": "hacked"}, timeout=5)
        assert r.status_code == 400

    def test_sessions_graceful_without_data(self):
        """无 Redis 或无数据时，应返回空列表而非 5xx"""
        r = requests.get(
            f"{ADMIN_BASE}/api/admin/sessions?chat_id=0&limit=10", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)

    def test_get_config_returns_shape(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/config", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert "default_provider" in body
        assert "providers" in body
        assert "network" in body

    def test_patch_config_updates(self):
        r = requests.patch(
            f"{ADMIN_BASE}/api/admin/config",
            json={"default_provider": "gemini"},
            timeout=5,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

# ─── 陪伴工具 ─────────────────────────────────────────────────────────────────

class TestCompanionTools:
    """任务3补齐 / feature: feat/companion-tools / 负责人: 第四位组员"""

    def test_health(self):
        r = requests.get(f"{COMP_BASE}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def _create_schedule(self) -> str:
        payload = {
            "chat_id": 999,
            "user_id": 999,
            "title": "契约测试提醒",
            "remind_at": "2099-12-31T09:00:00+08:00",
            "note": "自动化测试创建",
        }
        r = requests.post(f"{COMP_BASE}/api/schedule/add", json=payload, timeout=5)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "schedule_id" in body
        assert body["status"] == "scheduled"
        return body["schedule_id"]

    def test_schedule_create_returns_id(self):
        sid = self._create_schedule()
        assert sid  # 非空字符串

    def test_schedule_list_contains_created(self):
        sid = self._create_schedule()
        r = requests.get(f"{COMP_BASE}/api/schedule/list?chat_id=999", timeout=5)
        assert r.status_code == 200
        ids = [s["schedule_id"] for s in r.json().get("schedules", [])]
        assert sid in ids

    def test_schedule_delete(self):
        sid = self._create_schedule()
        r = requests.delete(f"{COMP_BASE}/api/schedule/{sid}", timeout=5)
        assert r.status_code == 200
        # 验证已删除
        r2 = requests.get(f"{COMP_BASE}/api/schedule/list?chat_id=999", timeout=5)
        ids = [s["schedule_id"] for s in r2.json().get("schedules", [])]
        assert sid not in ids

    def test_schedule_delete_not_found(self):
        r = requests.delete(f"{COMP_BASE}/api/schedule/nonexistent-uuid-xyz", timeout=5)
        assert r.status_code == 404
