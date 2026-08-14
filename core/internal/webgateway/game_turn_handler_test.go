package webgateway

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"testing"

	"go.uber.org/zap"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/engine"
	"betteragent-core/internal/idspace"
)

func newTestGameTurnServer(t *testing.T, token string, active bool) *Server {
	t.Helper()
	logger := zap.NewNop()

	// Offline-mode NatsBus (see nats_bridge_test.go's newTestNatsBridge for
	// why this is safe/fast: NewNatsBus gracefully degrades when it can't
	// connect, and handleGameTurn's PublishGameTurn call needs a real,
	// non-nil *bus.NatsBus).
	natsBus, err := bus.NewNatsBus("nats://127.0.0.1:1", "u", "p", logger)
	if err != nil {
		t.Fatalf("expected NewNatsBus to degrade gracefully to offline mode, got error: %v", err)
	}

	csm := engine.NewCentralStateMachine(logger)
	autonomousPlayState := engine.NewAutonomousPlayState()
	if active {
		autonomousPlayState.Activate(1001)
	}

	return &Server{
		gameEventToken:      token,
		autonomousPlayState: autonomousPlayState,
		bridge: &NatsBridge{
			bus:    natsBus,
			csm:    csm,
			logger: logger,
		},
		logger: logger,
	}
}

func postGameTurn(s *Server, token string, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("POST", "/api/game-turn", bytes.NewBufferString(body))
	if token != "" {
		req.Header.Set("X-Game-Event-Token", token)
	}
	rec := httptest.NewRecorder()
	s.handleGameTurn(rec, req)
	return rec
}

func decodeStatus(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	var resp map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to unmarshal response: %v (body=%s)", err, rec.Body.String())
	}
	status, _ := resp["status"].(string)
	return status
}

func TestHandleGameTurn_MissingTokenConfigured_Returns503(t *testing.T) {
	s := newTestGameTurnServer(t, "", true)
	rec := postGameTurn(s, "", `{"chat_id":1001}`)
	if rec.Code != 503 {
		t.Errorf("expected 503 when GAME_EVENT_TOKEN is unset, got %d", rec.Code)
	}
}

func TestHandleGameTurn_BadToken_Returns401(t *testing.T) {
	s := newTestGameTurnServer(t, "correct-token", true)
	rec := postGameTurn(s, "wrong-token", `{"chat_id":1001}`)
	if rec.Code != 401 {
		t.Errorf("expected 401 for invalid token, got %d", rec.Code)
	}
}

func TestHandleGameTurn_MissingChatID_Returns400(t *testing.T) {
	s := newTestGameTurnServer(t, "correct-token", false)
	s.autonomousPlayState.Activate(0)
	rec := postGameTurn(s, "correct-token", `{"reason":"map"}`)
	if rec.Code != 400 {
		t.Errorf("expected 400 for missing chat_id, got %d", rec.Code)
	}
}

func TestHandleGameTurn_InactiveWhenToggleOff(t *testing.T) {
	s := newTestGameTurnServer(t, "correct-token", false)
	rec := postGameTurn(s, "correct-token", `{"chat_id":1001,"reason":"map"}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if status := decodeStatus(t, rec); status != "inactive" {
		t.Errorf("expected status=inactive when autonomous play is off, got %q", status)
	}
}

func TestHandleGameTurn_BusyWhenChatNotIdle(t *testing.T) {
	s := newTestGameTurnServer(t, "correct-token", true)
	s.bridge.csm.TransitionToChat(1001, engine.StateThinking, "test_setup")

	rec := postGameTurn(s, "correct-token", `{"chat_id":1001,"reason":"map"}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if status := decodeStatus(t, rec); status != "busy" {
		t.Errorf("expected status=busy when chat is not IDLE, got %q", status)
	}
}

func TestHandleGameTurn_OkWhenActiveAndIdle(t *testing.T) {
	s := newTestGameTurnServer(t, "correct-token", true)
	// Chat starts IDLE by default (getOrCreateChatSM), no setup needed.
	rec := postGameTurn(s, "correct-token", `{"chat_id":1001,"reason":"map"}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if status := decodeStatus(t, rec); status != "ok" {
		t.Errorf("expected status=ok when active and idle, got %q", status)
	}
	// PublishGameTurn transitions the chat to StateThinking -- confirms it
	// actually ran, not just that the handler returned "ok".
	if got := s.bridge.csm.GetChatState(1001); got != engine.StateThinking {
		t.Errorf("expected chat to transition to StateThinking after a successful game turn trigger, got %s", got)
	}
}

func TestHandleGameTurn_WebNamespacedChatIDAllowed(t *testing.T) {
	s := newTestGameTurnServer(t, "correct-token", false)
	webChatID := idspace.WebNamespaceOffset + 1001
	s.autonomousPlayState.Activate(webChatID)
	rec := postGameTurn(s, "correct-token", `{"chat_id":9000000000001001,"reason":"map"}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if status := decodeStatus(t, rec); status != "ok" {
		t.Errorf("expected status=ok, got %q", status)
	}
	if got := s.bridge.csm.GetChatState(webChatID); got != engine.StateThinking {
		t.Errorf("expected chat to transition to StateThinking, got %s", got)
	}
}

func TestHandleGameTurn_WrongMethod_Returns405(t *testing.T) {
	s := newTestGameTurnServer(t, "correct-token", true)
	req := httptest.NewRequest("GET", "/api/game-turn", nil)
	req.Header.Set("X-Game-Event-Token", "correct-token")
	rec := httptest.NewRecorder()
	s.handleGameTurn(rec, req)
	if rec.Code != 405 {
		t.Errorf("expected 405 for GET, got %d", rec.Code)
	}
}
