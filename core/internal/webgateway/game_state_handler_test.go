package webgateway

import (
	"bytes"
	"net/http/httptest"
	"testing"

	"go.uber.org/zap"

	"betteragent-core/internal/engine"
)

func newTestGameStateServer(t *testing.T, token string, active bool) *Server {
	t.Helper()
	logger := zap.NewNop()

	autonomousPlayState := engine.NewAutonomousPlayState()
	if active {
		autonomousPlayState.Activate(1001)
	}

	sessions := newSessionManager(logger)

	return &Server{
		gameEventToken:      token,
		autonomousPlayState: autonomousPlayState,
		bridge: &NatsBridge{
			sessions: sessions,
			logger:   logger,
		},
		logger: logger,
	}
}

func postGameState(s *Server, token string, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("POST", "/api/game-state", bytes.NewBufferString(body))
	if token != "" {
		req.Header.Set("X-Game-Event-Token", token)
	}
	rec := httptest.NewRecorder()
	s.handleGameState(rec, req)
	return rec
}

func TestGameState_AuthRejection(t *testing.T) {
	s := newTestGameStateServer(t, "secret", true)

	rec := postGameState(s, "wrong-token", `{"floor": 1, "hp": 70, "max_hp": 70, "gold": 99, "act": 1}`)
	if rec.Code != 401 {
		t.Fatalf("expected 401 Unauthorized, got %d", rec.Code)
	}
}

func TestGameState_Success(t *testing.T) {
	s := newTestGameStateServer(t, "secret", true)

	rec := postGameState(s, "secret", `{"floor": 5, "hp": 65, "max_hp": 70, "gold": 120, "act": 1}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200 OK, got %d: %s", rec.Code, rec.Body.String())
	}
	status := decodeStatus(t, rec)
	if status != "ok" {
		t.Fatalf("expected status 'ok', got %q", status)
	}
}
