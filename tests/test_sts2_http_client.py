import pytest

from services.cognitive.tools.sts2_http_client import Sts2HttpClient, _trim_state


class FakeResponse:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class RaisingContextManager:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, get_responses=None, post_responses=None):
        self._get_responses = list(get_responses or [])
        self._post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []
        self.closed = False  # Sts2HttpClient._get_session checks this, matching real aiohttp.ClientSession

    def get(self, url, params=None, **kwargs):
        self.get_calls.append((url, params))
        return self._get_responses.pop(0)

    def post(self, url, json=None, **kwargs):
        self.post_calls.append((url, json))
        return self._post_responses.pop(0)


def _client_with_fake_session(session: FakeSession) -> Sts2HttpClient:
    client = Sts2HttpClient(api_url="http://127.0.0.1:15526/api/v1/singleplayer")
    client._session = session  # bypass lazy aiohttp.ClientSession construction
    return client


@pytest.mark.asyncio
async def test_get_state_success():
    session = FakeSession(get_responses=[FakeResponse(200, {"state_type": "map", "run": {"floor": 3}})])
    client = _client_with_fake_session(session)

    result = await client.get_state()

    assert result["status"] == "ok"
    assert result["state_type"] == "map"


@pytest.mark.asyncio
async def test_get_state_non_200_becomes_error_dict_not_raise():
    session = FakeSession(get_responses=[FakeResponse(500, {"error": "boom"})])
    client = _client_with_fake_session(session)

    result = await client.get_state()

    assert result["status"] == "error"
    assert "500" in result["message"]


@pytest.mark.asyncio
async def test_get_state_connection_error_becomes_error_dict_not_raise():
    session = FakeSession(get_responses=[RaisingContextManager(ConnectionError("refused"))])
    client = _client_with_fake_session(session)

    result = await client.get_state()

    assert result["status"] == "error"
    assert "unreachable" in result["message"]


@pytest.mark.asyncio
async def test_post_action_success_folds_in_trimmed_followup_state():
    session = FakeSession(
        post_responses=[FakeResponse(200, {"status": "ok", "message": "played"})],
        get_responses=[FakeResponse(200, {
            "status": "ok",
            "state_type": "monster",
            "battle": {"turn": "player", "is_play_phase": True},
            "player": {"hp": 40, "max_hp": 70, "energy": 2, "hand": [1, 2], "gold": 99, "relics": ["x"]},
        })],
    )
    client = _client_with_fake_session(session)

    result = await client.post_action("play_card", {"card_index": 0, "target": None})

    # None-valued args (e.g. an omitted optional target) are dropped before
    # posting, not sent as an explicit null.
    assert session.post_calls == [("http://127.0.0.1:15526/api/v1/singleplayer", {"action": "play_card", "card_index": 0})]
    assert result["status"] == "ok"
    assert result["state"]["state_type"] == "monster"
    # Trimmed: relics/gold-in-full-state stay out of the top level, but the
    # player subset (hp/max_hp/energy/hand) comes through.
    assert result["state"]["player"] == {"hp": 40, "max_hp": 70, "energy": 2, "hand": [1, 2]}
    assert "relics" not in result["state"]["player"]


@pytest.mark.asyncio
async def test_post_action_never_raises_on_connection_error():
    session = FakeSession(post_responses=[RaisingContextManager(TimeoutError("slow"))])
    client = _client_with_fake_session(session)

    result = await client.post_action("end_turn", {})

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_end_turn_retries_when_godot_ui_still_shows_player_turn_zero_energy():
    # Regression test for a bug in the stuck-end-turn-button retry guard:
    # get_state() spreads the mod's fields directly into the top level
    # (return {"status": "ok", **data}) -- the retry check must read
    # state.get("battle")/state.get("player") directly, not state.get("data").
    # First follow-up GET looks stuck (still player's turn, 0 energy);
    # second (post-retry) GET shows the turn actually ended.
    session = FakeSession(
        post_responses=[
            FakeResponse(200, {"status": "ok", "message": "turn ended"}),  # initial end_turn
            FakeResponse(200, {"status": "ok", "message": "turn ended (retry)"}),  # retried end_turn
        ],
        get_responses=[
            FakeResponse(200, {
                "status": "ok", "state_type": "monster",
                "battle": {"turn": "player", "is_play_phase": True},
                "player": {"hp": 40, "max_hp": 70, "energy": 0, "hand": []},
            }),
            FakeResponse(200, {
                "status": "ok", "state_type": "monster",
                "battle": {"turn": "enemy", "is_play_phase": False},
                "player": {"hp": 40, "max_hp": 70, "energy": 0, "hand": []},
            }),
        ],
    )
    client = _client_with_fake_session(session)

    result = await client.post_action("end_turn", {})

    assert len(session.post_calls) == 2  # initial + retry
    assert result["state"]["battle"]["turn"] == "enemy"  # picked up the post-retry state


def test_trim_state_keeps_only_expected_keys():
    trimmed = _trim_state({
        "status": "ok",
        "state_type": "monster",
        "battle": {"turn": "player", "is_play_phase": True},
        "run": {"act": 1, "floor": 5},
        "player": {"hp": 10, "max_hp": 70, "energy": 3, "hand": [], "relics": ["a", "b"], "gold": 50},
    })
    assert set(trimmed.keys()) <= {"state_type", "battle", "player"}
    assert trimmed["player"] == {"hp": 10, "max_hp": 70, "energy": 3, "hand": []}
