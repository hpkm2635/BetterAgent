package webgateway

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"regexp"

	"go.uber.org/zap"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/engine"
	"betteragent-core/internal/schema"
)

// GameEventWeights is the config-driven (game, event_type) -> Urge weight
// table for POST /api/game-event. Weight is always resolved server-side
// from this table -- the client only ever names an event, never supplies a
// weight directly.
type GameEventWeights struct {
	DefaultWeight float64
	Games         map[string]map[string]float64
}

func (w GameEventWeights) lookup(game, eventType string) float64 {
	if perGame, ok := w.Games[game]; ok {
		if weight, ok := perGame[eventType]; ok {
			return weight
		}
	}
	return w.DefaultWeight
}

var gameEventNamePattern = regexp.MustCompile(`^[a-z0-9_]{1,64}$`)

const gameEventMaxBodyBytes = 8 * 1024
const gameEventDetailMaxLen = 500

type gameEventRequest struct {
	Game      string                 `json:"game"`
	EventType string                 `json:"event_type"`
	Detail    string                 `json:"detail,omitempty"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
}

// handleGameEvent is POST /api/game-event, served on a dedicated
// loopback-only listener (see gameEventBindAddr in server.go) so a leaked
// GAME_EVENT_TOKEN alone can't be used to inject fake events from off-box --
// the attacker also needs local access, matching how this codebase draws
// its other trust boundaries (docs/SECURITY.md).
func (s *Server) handleGameEvent(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// This integration is optional -- unlike WEBGATEWAY_TOKEN, its absence
	// must not Fatal the whole core, just disable the endpoint.
	if s.gameEventToken == "" {
		writeGameEventJSON(w, http.StatusServiceUnavailable, map[string]interface{}{"error": "game event ingestion disabled"})
		return
	}

	// Auth check before any body read, mirroring handleWebSocket's
	// pre-upgrade token check.
	suppliedToken := r.Header.Get("X-Game-Event-Token")
	if subtle.ConstantTimeCompare([]byte(suppliedToken), []byte(s.gameEventToken)) != 1 {
		s.logger.Warn("Rejected game event with invalid/missing token", zap.String("remote_addr", r.RemoteAddr))
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, gameEventMaxBodyBytes)
	var req gameEventRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			http.Error(w, `{"error":"request body too large"}`, http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
		return
	}

	if !gameEventNamePattern.MatchString(req.Game) || !gameEventNamePattern.MatchString(req.EventType) {
		http.Error(w, `{"error":"game and event_type must match ^[a-z0-9_]{1,64}$"}`, http.StatusBadRequest)
		return
	}

	if len(req.Detail) > gameEventDetailMaxLen {
		req.Detail = req.Detail[:gameEventDetailMaxLen]
	}

	weight := s.gameEventWeights.lookup(req.Game, req.EventType)

	var currentUrge float64
	if s.urgeEngine != nil {
		s.urgeEngine.RecordGameEvent(weight, req.Detail)
		currentUrge = s.urgeEngine.CurrentValue()
	}

	s.logger.Info("🎮 Game event received",
		zap.String("game", req.Game),
		zap.String("event_type", req.EventType),
		zap.Float64("weight", weight),
	)

	if s.bridge != nil && s.bridge.bus != nil {
		var detailPtr *string
		if req.Detail != "" {
			detailPtr = &req.Detail
		}
		payload := schema.GameEventPayload{
			BasePayload: schema.NewBasePayload("webgateway_game_event"),
			Game:        req.Game,
			EventType:   req.EventType,
			Weight:      weight,
			Detail:      detailPtr,
			Metadata:    req.Metadata,
		}
		if err := s.bridge.bus.Publish(bus.SubjectGameEvent, "webgateway_game_event", payload); err != nil {
			// The Urge side effect above already happened -- this publish is
			// only for observability/future consumers, so log and move on
			// rather than failing the HTTP response over it.
			s.logger.Error("Failed to publish GameEvent to NATS", zap.Error(err))
		}

		// Critical Game Events (death/victory): Instantly trigger proactive reaction
		// without waiting for ClockEngine's 30s tick or being suppressed by cooldown.
		if req.EventType == "death" || req.EventType == "victory" {
			targetChatID := int64(0)
			if s.autonomousPlayState != nil {
				targetChatID = s.autonomousPlayState.TargetChatID()
			}
			if targetChatID == 0 && s.urgeEngine != nil {
				targetChatID = s.urgeEngine.PrimaryChatID()
			}
			if targetChatID != 0 {
				reason := req.Detail
				if reason == "" {
					reason = "run ended (" + req.EventType + ")"
				}
				engine.PublishProactiveTurn(s.bridge.bus, s.bridge.csm, s.bridge.emotionalState, s.bridge.personality, s.bridge.circadian, targetChatID, reason, s.logger)
				s.logger.Info("🎮 Instant game over reaction triggered", zap.String("event_type", req.EventType), zap.Int64("chat_id", targetChatID), zap.String("reason", reason))
			}
		}
	}

	writeGameEventJSON(w, http.StatusOK, map[string]interface{}{
		"status":       "ok",
		"urge_added":   weight,
		"current_urge": currentUrge,
	})
}

func writeGameEventJSON(w http.ResponseWriter, status int, body map[string]interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
