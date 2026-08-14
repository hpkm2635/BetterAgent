package bus

import (
	"testing"

	"betteragent-core/internal/idspace"
)

func TestActionDecisionSubject(t *testing.T) {
	cases := []struct {
		name    string
		channel string
		chatID  int64
		want    string
	}{
		{"web positive chat_id", "web", 1001, "agent.action.web.1001"},
		{"telegram negative chat_id (channel-style)", "telegram", -1000000000123, "agent.action.telegram.-1000000000123"},
		{"web namespaced chat_id", "web", idspace.WebNamespaceOffset + 1001, "agent.action.web.9000000000001001"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := ActionDecisionSubject(tc.channel, tc.chatID)
			if got != tc.want {
				t.Errorf("ActionDecisionSubject(%q, %d) = %q, want %q", tc.channel, tc.chatID, got, tc.want)
			}
		})
	}
}

func TestActionDecisionWildcard(t *testing.T) {
	cases := []struct {
		channel string
		want    string
	}{
		{"web", "agent.action.web.*"},
		{"telegram", "agent.action.telegram.*"},
		{"discord", "agent.action.discord.*"}, // forward-compat: no hardcoded enum
	}
	for _, tc := range cases {
		got := ActionDecisionWildcard(tc.channel)
		if got != tc.want {
			t.Errorf("ActionDecisionWildcard(%q) = %q, want %q", tc.channel, got, tc.want)
		}
	}
}
