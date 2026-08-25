"""Regression coverage for admin/backend's persona-activate endpoint and the
downstream shared.persona_loader hot-reload propagation it depends on.

Prior to this fix, POST /api/admin/personas/{id}/activate crashed with
TypeError: _publish_persona_update() missing 1 required positional argument:
'patch' -- activate_persona called it with just persona_id, but the function
signature requires a (persona_id, patch) pair (see patch_persona's correct
call for comparison). Fixing just the call signature would still leave the
feature silently broken: PersonaLoader.handle_persona_update used to bail out
early on an empty patch (which an activation-only message legitimately has,
since it isn't changing any persona fields) without invalidating either its
own cache or shared.config_loader's process-wide config.yaml cache -- so a
running cognitive service would never actually pick up the new persona.active
value written to disk, even though the endpoint reported "hot_reload: ok".
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import admin.backend.main as admin_main
from admin.backend.main import app

from shared import config_loader
from shared.persona_loader import PersonaLoader


@pytest.fixture()
def client(tmp_path):
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    (persona_dir / "catgirl.yaml").write_text("id: catgirl\nname: Camelia\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("persona:\n  active: catgirl\n", encoding="utf-8")

    with patch.object(admin_main, "ADMIN_SECRET_KEY", ""), \
         patch.object(admin_main, "PERSONA_DIR", persona_dir), \
         patch.object(admin_main, "REPO_ROOT", tmp_path):
        yield TestClient(app, raise_server_exceptions=False)


def test_activate_persona_no_longer_crashes_and_reports_hot_reload_ok(client, tmp_path):
    with patch("admin.backend.main._nats_publish", new_callable=AsyncMock) as mock_publish:
        mock_publish.return_value = True
        resp = client.post("/api/admin/personas/catgirl/activate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["active_id"] == "catgirl"
    assert body["hot_reload"] == "ok"

    written = (tmp_path / "config" / "config.yaml").read_text(encoding="utf-8")
    assert "active: catgirl" in written

    assert mock_publish.await_count == 1
    subject, payload = mock_publish.await_args.args
    assert subject == "agent.persona.update"
    assert payload == {"persona_id": "catgirl"}


def test_activate_persona_reports_hot_reload_failed_when_nats_unreachable(client):
    with patch("admin.backend.main._nats_publish", new_callable=AsyncMock) as mock_publish:
        mock_publish.return_value = False
        resp = client.post("/api/admin/personas/catgirl/activate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["hot_reload"] == "failed"


def test_activate_persona_unknown_id_returns_404_not_500(client):
    resp = client.post("/api/admin/personas/does-not-exist/activate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_handle_persona_update_activation_only_invalidates_config_cache():
    """An activation-only message (persona_id, no patchable fields) must still
    invalidate config_loader's process-wide config.yaml cache -- otherwise
    PersonaLoader.load_active_persona()'s persona.active read stays stuck on
    whatever was cached before activation, even after PersonaLoader's own
    cache is cleared."""
    PersonaLoader.invalidate_cache()
    with patch.object(config_loader, "invalidate_cache") as mock_cfg_invalidate:
        await PersonaLoader.handle_persona_update(json.dumps({"persona_id": "patra2"}).encode("utf-8"))
    mock_cfg_invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_handle_persona_update_activation_only_still_invalidates_persona_cache():
    PersonaLoader._cached_persona = {"name": "stale"}
    PersonaLoader._last_active_id = "catgirl"

    await PersonaLoader.handle_persona_update(json.dumps({"persona_id": "patra2"}).encode("utf-8"))

    assert PersonaLoader._cached_persona == {}
    assert PersonaLoader._last_active_id == ""
