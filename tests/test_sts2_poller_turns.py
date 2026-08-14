import asyncio

import pytest

from services.game_watcher.sts2_poller import poll_once, RunTracker


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


class FakeSession:
    def __init__(self, states):
        self.states = states
        self.i = 0
        self.game_turn_posts = []
        self.game_event_posts = []

    def get(self, url, params=None, timeout=None):
        state = self.states[self.i]
        self.i += 1
        return FakeResponse(200, state)

    def post(self, url, json=None, headers=None, timeout=None):
        if url.endswith("/api/game-turn"):
            self.game_turn_posts.append(json)
        else:
            self.game_event_posts.append(json)
        return FakeResponse(200, {"status": "ok"})


async def _run_poll(states, target_chat_id=1001, token="test-token"):
    tracker = RunTracker()
    session = FakeSession(states)
    for _ in range(len(states)):
        await poll_once(
            session,
            "http://fake/singleplayer",
            "http://fake/api/game-event",
            "http://fake/api/game-turn",
            token,
            0.15,
            {"rare", "boss"},
            target_chat_id,
            tracker,
        )
    return session, tracker


def test_map_screen_triggers_one_game_turn():
    states = [
        {"state_type": "map", "run": {"floor": 1}, "player": {"hp": 70, "max_hp": 70, "gold": 99, "relics": []}},
    ]
    session, _ = asyncio.run(_run_poll(states))
    assert len(session.game_turn_posts) == 1
    assert session.game_turn_posts[0]["chat_id"] == 1001
    assert session.game_turn_posts[0]["reason"] == "map"


def test_unchanged_actionable_state_does_not_refire():
    state = {"state_type": "map", "run": {"floor": 1}, "player": {"hp": 70, "max_hp": 70, "gold": 99, "relics": []}}
    states = [state, dict(state), dict(state)]  # same signature every poll
    session, _ = asyncio.run(_run_poll(states))
    assert len(session.game_turn_posts) == 1


def test_menu_and_unknown_never_trigger_a_turn():
    states = [
        {"state_type": "menu"},
        {"state_type": "unknown"},
    ]
    session, _ = asyncio.run(_run_poll(states))
    assert session.game_turn_posts == []


def test_combat_only_triggers_on_player_turn_with_play_phase():
    states = [
        # Enemy's turn -- not actionable.
        {
            "state_type": "monster", "run": {"floor": 5},
            "player": {"hp": 40, "max_hp": 70, "energy": 3, "hand": [1, 2], "gold": 0, "relics": []},
            "battle": {"turn": "enemy", "is_play_phase": False},
        },
        # Now it's the player's turn.
        {
            "state_type": "monster", "run": {"floor": 5},
            "player": {"hp": 40, "max_hp": 70, "energy": 3, "hand": [1, 2], "gold": 0, "relics": []},
            "battle": {"turn": "player", "is_play_phase": True},
        },
    ]
    session, _ = asyncio.run(_run_poll(states))
    assert len(session.game_turn_posts) == 1
    assert session.game_turn_posts[0]["reason"] == "monster_player_turn"


def test_combat_state_changing_hand_refires():
    base_player = {"hp": 40, "max_hp": 70, "energy": 3, "gold": 0, "relics": []}
    states = [
        {"state_type": "monster", "run": {"floor": 5}, "player": {**base_player, "hand": [1, 2, 3]}, "battle": {"turn": "player", "is_play_phase": True}},
        # Played a card -- hand shrank, energy dropped: new signature, should refire.
        {"state_type": "monster", "run": {"floor": 5}, "player": {**base_player, "hand": [1, 2], "energy": 2}, "battle": {"turn": "player", "is_play_phase": True}},
    ]
    session, _ = asyncio.run(_run_poll(states))
    assert len(session.game_turn_posts) == 2


def test_game_over_never_triggers_a_game_turn():
    states = [
        {"state_type": "monster", "run": {"floor": 5}, "player": {"hp": 0, "max_hp": 70, "energy": 0, "hand": [], "gold": 0, "relics": []}, "battle": {"turn": "player", "is_play_phase": True}},
        {"state_type": "game_over"},
    ]
    session, _ = asyncio.run(_run_poll(states))
    # The lethal combat poll before death may legitimately fire one turn
    # (it's a real player-turn decision point) -- the point of this test is
    # that "game_over" itself is not in ACTIONABLE_STATE_TYPES and never
    # produces a *second* trigger for that poll.
    assert len(session.game_turn_posts) <= 1
    assert all(p["reason"] != "game_over" for p in session.game_turn_posts)


def test_none_target_chat_id_disables_triggering_entirely():
    states = [
        {"state_type": "map", "run": {"floor": 1}, "player": {"hp": 70, "max_hp": 70, "gold": 99, "relics": []}},
    ]
    session, _ = asyncio.run(_run_poll(states, target_chat_id=None))
    assert session.game_turn_posts == []
