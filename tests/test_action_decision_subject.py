from shared.subjects import action_decision_subject, action_decision_wildcard


def test_action_decision_subject_web_positive_chat_id():
    assert action_decision_subject("web", 1001) == "agent.action.web.1001"


def test_action_decision_subject_telegram_negative_chat_id():
    # Telegram channel/supergroup IDs are sometimes negative in this codebase
    # (see core/internal/gotd/adapter.go's peer-cache keying) -- confirm the
    # hyphen doesn't break subject construction.
    assert action_decision_subject("telegram", -1000000000123) == "agent.action.telegram.-1000000000123"


def test_action_decision_subject_web_namespaced_chat_id():
    web_namespace_offset = 9_000_000_000_000_000
    assert action_decision_subject("web", web_namespace_offset + 1001) == "agent.action.web.9000000000001001"


def test_action_decision_wildcard():
    assert action_decision_wildcard("web") == "agent.action.web.*"
    assert action_decision_wildcard("telegram") == "agent.action.telegram.*"
    # No hardcoded channel enum -- a future platform needs zero changes here.
    assert action_decision_wildcard("discord") == "agent.action.discord.*"
