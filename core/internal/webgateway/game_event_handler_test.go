package webgateway

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"go.uber.org/zap"

	"betteragent-core/internal/engine"
)

func newTestGameEventServer(token string) *Server {
	return &Server{
		gameEventToken: token,
		gameEventWeights: GameEventWeights{
			DefaultWeight: 0.1,
			Games: map[string]map[string]float64{
				"slay_the_spire_2": {
					"rare_relic_pickup": 0.6,
					"victory":           1.0,
				},
			},
		},
		urgeEngine: engine.NewUrgeEngine(engine.UrgeParams{UrgeCap: 100}, zap.NewNop()),
		logger:     zap.NewNop(),
	}
}

func postGameEvent(s *Server, token string, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("POST", "/api/game-event", bytes.NewBufferString(body))
	if token != "" {
		req.Header.Set("X-Game-Event-Token", token)
	}
	rec := httptest.NewRecorder()
	s.handleGameEvent(rec, req)
	return rec
}

func TestHandleGameEvent_MissingTokenConfigured_Returns503(t *testing.T) {
	s := newTestGameEventServer("")
	rec := postGameEvent(s, "", `{"game":"slay_the_spire_2","event_type":"victory"}`)
	if rec.Code != 503 {
		t.Errorf("expected 503 when GAME_EVENT_TOKEN is unset, got %d", rec.Code)
	}
}

func TestHandleGameEvent_BadToken_Returns401BeforeBodyRead(t *testing.T) {
	s := newTestGameEventServer("correct-token")
	rec := postGameEvent(s, "wrong-token", `{"game":"slay_the_spire_2","event_type":"victory"}`)
	if rec.Code != 401 {
		t.Errorf("expected 401 for invalid token, got %d", rec.Code)
	}
	if s.urgeEngine.CurrentValue() != 0 {
		t.Errorf("expected no Urge side effect for a rejected request, got %f", s.urgeEngine.CurrentValue())
	}
}

func TestHandleGameEvent_MissingToken_Returns401(t *testing.T) {
	s := newTestGameEventServer("correct-token")
	rec := postGameEvent(s, "", `{"game":"slay_the_spire_2","event_type":"victory"}`)
	if rec.Code != 401 {
		t.Errorf("expected 401 for missing token, got %d", rec.Code)
	}
}

func TestHandleGameEvent_OversizedBody_Returns413(t *testing.T) {
	s := newTestGameEventServer("correct-token")
	huge := strings.Repeat("a", gameEventMaxBodyBytes+1)
	body := `{"game":"slay_the_spire_2","event_type":"victory","detail":"` + huge + `"}`
	rec := postGameEvent(s, "correct-token", body)
	if rec.Code != 413 {
		t.Errorf("expected 413 for oversized body, got %d", rec.Code)
	}
}

func TestHandleGameEvent_InvalidJSON_Returns400(t *testing.T) {
	s := newTestGameEventServer("correct-token")
	rec := postGameEvent(s, "correct-token", `not json`)
	if rec.Code != 400 {
		t.Errorf("expected 400 for invalid JSON, got %d", rec.Code)
	}
}

func TestHandleGameEvent_InvalidGameOrEventTypeCharset_Returns400(t *testing.T) {
	s := newTestGameEventServer("correct-token")
	rec := postGameEvent(s, "correct-token", `{"game":"Slay The Spire 2!","event_type":"victory"}`)
	if rec.Code != 400 {
		t.Errorf("expected 400 for game name outside ^[a-z0-9_]{1,64}$, got %d", rec.Code)
	}
}

func TestHandleGameEvent_ValidRequest_UsesConfigResolvedWeightNotClientSupplied(t *testing.T) {
	s := newTestGameEventServer("correct-token")

	// Client attempts to smuggle a custom weight -- must be ignored; the
	// config-resolved weight (0.6 for rare_relic_pickup) is what counts.
	rec := postGameEvent(s, "correct-token", `{"game":"slay_the_spire_2","event_type":"rare_relic_pickup","weight":999,"detail":"got a rare relic"}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to unmarshal response: %v", err)
	}
	if resp["urge_added"] != 0.6 {
		t.Errorf("expected urge_added=0.6 (config-resolved), got %v", resp["urge_added"])
	}
}

func TestHandleGameEvent_UnknownEventType_FallsBackToDefaultWeight(t *testing.T) {
	s := newTestGameEventServer("correct-token")
	rec := postGameEvent(s, "correct-token", `{"game":"slay_the_spire_2","event_type":"totally_unknown_event"}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200 for unknown event_type (should fall back, not reject), got %d: %s", rec.Code, rec.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to unmarshal response: %v", err)
	}
	if resp["urge_added"] != 0.1 {
		t.Errorf("expected fallback to default_weight=0.1, got %v", resp["urge_added"])
	}
}

func TestHandleGameEvent_WrongMethod_Returns405(t *testing.T) {
	s := newTestGameEventServer("correct-token")
	req := httptest.NewRequest("GET", "/api/game-event", nil)
	req.Header.Set("X-Game-Event-Token", "correct-token")
	rec := httptest.NewRecorder()
	s.handleGameEvent(rec, req)
	if rec.Code != 405 {
		t.Errorf("expected 405 for GET, got %d", rec.Code)
	}
}
