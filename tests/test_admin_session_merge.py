"""Unit tests for admin/backend's session-record merging.

The cognitive engine streams one ActionDecision per sentence and the memory
service appends each to the short-term buffer, so a single assistant reply
shows up as many records in the admin session view. _merge_consecutive_same_role
joins consecutive same-role entries into one "turn" and collapses exact
consecutive duplicates (the same final sentence is sometimes published twice).
"""

from admin.backend.main import _merge_consecutive_same_role


def _msg(role: str, content: str, index: int) -> dict:
    return {"message_id": index, "role": role, "content": content, "timestamp": float(index)}


def test_merges_consecutive_assistant_sentences_into_one_reply():
    items = [
        _msg("assistant", "哼，主人怎么连人家的名字都忘了吗？", 0),
        _msg("assistant", "真是的，我是Camelia呀！", 1),
        _msg("assistant", "才、才不是特意要提醒你我的名字的，", 2),
    ]
    merged = _merge_consecutive_same_role(items)
    assert len(merged) == 1
    assert merged[0]["role"] == "assistant"
    assert merged[0]["content"] == "哼，主人怎么连人家的名字都忘了吗？真是的，我是Camelia呀！才、才不是特意要提醒你我的名字的，"
    # Keeps the first entry's identity.
    assert merged[0]["message_id"] == 0


def test_keeps_user_and_assistant_turns_separate():
    items = [
        _msg("user", "[2026-08-27 08:39] 你叫什么名字", 0),
        _msg("assistant", "哼，主人怎么连人家的名字都忘了吗？", 1),
        _msg("assistant", "真是的，我是Camelia呀！", 2),
    ]
    merged = _merge_consecutive_same_role(items)
    assert len(merged) == 2
    assert merged[0]["role"] == "user"
    assert merged[1]["role"] == "assistant"
    assert merged[1]["content"] == "哼，主人怎么连人家的名字都忘了吗？真是的，我是Camelia呀！"


def test_collapses_exact_consecutive_duplicates():
    items = [
        _msg("assistant", "喵~", 0),
        _msg("assistant", "喵~", 1),
        _msg("assistant", "暂时没有特别的推荐喵~", 2),
        _msg("assistant", "暂时没有特别的推荐喵~", 3),
        _msg("assistant", "暂时没有特别的推荐喵~", 4),
    ]
    merged = _merge_consecutive_same_role(items)
    assert len(merged) == 1
    assert merged[0]["content"] == "喵~暂时没有特别的推荐喵~"


def test_interleaved_turns_are_not_over_merged():
    items = [
        _msg("user", "在吗", 0),
        _msg("assistant", "在的喵~", 1),
        _msg("user", "一起打游戏？", 2),
        _msg("assistant", "好呀好呀！", 3),
        _msg("assistant", "我可是很厉害的哦！", 4),
    ]
    merged = _merge_consecutive_same_role(items)
    assert len(merged) == 4
    assert [m["role"] for m in merged] == ["user", "assistant", "user", "assistant"]
    assert merged[3]["content"] == "好呀好呀！我可是很厉害的哦！"


def test_empty_and_blank_content_are_dropped():
    items = [_msg("assistant", "", 0), _msg("assistant", "  ", 1), _msg("assistant", "喵~", 2)]
    merged = _merge_consecutive_same_role(items)
    assert len(merged) == 1
    assert merged[0]["content"] == "喵~"
