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
import os

import pytest
import requests

KB_BASE    = "http://localhost:8093"
ADMIN_BASE = "http://localhost:8094"
COMP_BASE  = "http://localhost:8096"

# admin/backend/main.py's enforce_admin_token middleware requires this
# whenever ADMIN_SECRET_KEY is set in the deployment's .env (which it is by
# default -- see admin/backend/.env.example) -- without it every TestAdminPanel
# request 401s instead of exercising the actual endpoint logic. Empty when
# ADMIN_SECRET_KEY isn't set, matching the middleware's own no-auth-configured
# passthrough for local dev.
ADMIN_HEADERS = {"X-Admin-Token": os.environ.get("ADMIN_SECRET_KEY", "")}

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
        # /health is intentionally exempt from enforce_admin_token (liveness
        # probes shouldn't need a secret), so no ADMIN_HEADERS here.
        r = requests.get(f"{ADMIN_BASE}/health", timeout=5)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_list_personas_contains_both(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas", timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()["personas"]]
        assert "catgirl" in ids
        assert "patra" in ids

    def test_get_persona_detail_shape(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "catgirl"
        assert "name" in body
        assert "base_prompt" in body

    def test_get_persona_not_found(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/personas/not_exist_xyz", timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 404

    def test_patch_persona_allowed_field(self):
        # 备份原始 name
        original = requests.get(
            f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5, headers=ADMIN_HEADERS).json()["name"]
        # 修改
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"name": "__contract_test__"}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"
        # 验证实际写入
        updated = requests.get(
            f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5, headers=ADMIN_HEADERS).json()["name"]
        assert updated == "__contract_test__"
        # 恢复
        requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                       json={"name": original}, timeout=5, headers=ADMIN_HEADERS)

    def test_patch_persona_tts_allowed_subfield(self):
        # tts.prompt_lang/prompt_audio/prompt_text/text_lang are the fields
        # GPT-SoVITS re-reads on every synthesis call, so these hot-reload
        # for real; provider/voice_id are read once at TTS service startup
        # and stay forbidden (see next test).
        original = requests.get(
            f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5, headers=ADMIN_HEADERS).json()["tts"]["prompt_lang"]
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"tts": {"prompt_lang": "__contract_test__"}}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"
        updated = requests.get(
            f"{ADMIN_BASE}/api/admin/personas/catgirl", timeout=5, headers=ADMIN_HEADERS).json()["tts"]
        assert updated["prompt_lang"] == "__contract_test__"
        # A partial tts patch must merge into the existing object, not
        # replace it -- provider/voice_id (and every other untouched
        # subfield) must survive unchanged.
        assert "provider" in updated
        assert "voice_id" in updated
        # 恢复
        requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                       json={"tts": {"prompt_lang": original}}, timeout=5, headers=ADMIN_HEADERS)

    def test_patch_persona_forbidden_tts_provider(self):
        # provider/voice_id are read once at TTS service startup and cached
        # in memory -- allowing them through PATCH would silently do nothing
        # (or worse, look like it worked) without a service restart, so
        # they're rejected rather than accepted-but-ineffective.
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"tts": {"provider": "evil"}}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 400
        assert "error" in r.json()

    def test_patch_persona_forbidden_tts_not_an_object(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"tts": "not-an-object"}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 400
        assert "error" in r.json()

    def test_patch_persona_forbidden_id(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/personas/catgirl",
                           json={"id": "hacked"}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 400

    def test_sessions_graceful_without_data(self):
        """无 Redis 或无数据时，应返回空列表而非 5xx"""
        r = requests.get(
            f"{ADMIN_BASE}/api/admin/sessions?chat_id=0&limit=10", timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)

    # ─── 2.7 系统配置与 API 密钥管理（BYOK 模式）───

    def test_get_config_shape(self):
        r = requests.get(f"{ADMIN_BASE}/api/admin/config", timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "default_provider" in body
        assert "network" in body
        names = [p["name"] for p in body["providers"]]
        assert "gemini" in names
        assert "claude" in names
        assert "qwen" in names

    def test_patch_config_default_provider_roundtrip(self):
        # 备份原始 default_provider
        original = requests.get(
            f"{ADMIN_BASE}/api/admin/config", timeout=5, headers=ADMIN_HEADERS).json()["default_provider"]
        # 修改
        r = requests.patch(f"{ADMIN_BASE}/api/admin/config",
                           json={"default_provider": "claude"}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        # 验证实际写入
        updated = requests.get(
            f"{ADMIN_BASE}/api/admin/config", timeout=5, headers=ADMIN_HEADERS).json()["default_provider"]
        assert updated == "claude"
        # 恢复
        requests.patch(f"{ADMIN_BASE}/api/admin/config",
                       json={"default_provider": original}, timeout=5, headers=ADMIN_HEADERS)

    def test_patch_config_unknown_provider(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/config",
                           json={"providers": {"evil": {"api_key": "x"}}}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 400
        assert "error" in r.json()

    def test_patch_config_forbidden_field(self):
        r = requests.patch(f"{ADMIN_BASE}/api/admin/config",
                           json={"unknown_field": 1}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 400
        assert "error" in r.json()

    def test_test_key_unknown_provider(self):
        r = requests.post(f"{ADMIN_BASE}/api/admin/config/test-key",
                          json={"provider": "evil", "api_key": "x"}, timeout=5, headers=ADMIN_HEADERS)
        assert r.status_code == 400
        assert "error" in r.json()

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
