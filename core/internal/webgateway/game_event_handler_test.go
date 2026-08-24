package webgateway

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"go.uber.org/zap"

	"betteragent-core/internal/emotion"
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

// TestHandleGameEvent_DeathEvent_UsesTargetChatsOwnEmotionalState covers the
// fix for a bug where the instant death/victory reaction always read
// s.bridge.emotionalState (a single global fallback instance) instead of the
// target chat's own EmotionalState from s.bridge.emotionStore -- so a
// critical-event reaction could describe the wrong chat's mood.
//
// Note this can't be a black-box "fails on the old code" regression test:
// engine.PublishProactiveTurn nil-checks its emoState argument before using
// it, so passing the wrong (or even a nil) EmotionalState doesn't panic or
// otherwise produce an externally observable difference through the HTTP
// response -- and it takes a concrete *bus.NatsBus, so there's no interface
// seam to substitute a spy that could capture which pointer it received.
// This instead (a) unit-tests the resolver the fixed call site now uses,
// confirming it returns the target chat's own (distinct-by-pointer)
// instance rather than the global fallback, and (b) exercises the full HTTP
// path with a populated emotionStore to catch wiring/nil-pointer regressions.
func TestHandleGameEvent_DeathEvent_UsesTargetChatsOwnEmotionalState(t *testing.T) {
	s := newTestGameEventServer("correct-token")

	globalFallback := emotion.NewEmotionalState()
	store := emotion.NewEmotionalStateStore(emotion.NewPersonalityFromConfig(nil))

	const targetChatID = int64(777)
	perChatState := store.GetOrCreate(targetChatID)
	if perChatState == globalFallback {
		t.Fatalf("test setup invalid: per-chat state must be a distinct instance from the global fallback")
	}

	nb, _ := newTestNatsBridge(t)
	nb.emotionalState = globalFallback
	nb.SetEmotionStore(store)
	s.bridge = nb
	s.autonomousPlayState = engine.NewAutonomousPlayState()
	s.autonomousPlayState.Activate(targetChatID)

	// The bug and its fix are both about *which* EmotionalState pointer gets
	// resolved for the reaction -- assert the resolver the fixed call site
	// now uses returns the target chat's own instance, not the global one.
	if got := s.bridge.getEmotionalStateForChat(targetChatID); got != perChatState {
		t.Errorf("expected getEmotionalStateForChat(%d) to return the chat's own EmotionalState instance, got a different pointer", targetChatID)
	}

	// End-to-end: triggering the death event must not panic and must
	// actually reach the proactive-turn path (CSM transitions the target
	// chat to THINKING), proving the wiring through the fixed call site
	// works, not just the resolver in isolation.
	rec := postGameEvent(s, "correct-token", `{"game":"slay_the_spire_2","event_type":"death","detail":"died to Act 3 boss"}`)
	if rec.Code != 200 {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if got := s.bridge.csm.GetChatState(targetChatID); got != engine.StateThinking {
		t.Errorf("expected target chat %d to transition to THINKING after death event, got %s", targetChatID, got)
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
