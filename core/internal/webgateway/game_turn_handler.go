package webgateway

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"

	"go.uber.org/zap"

	"betteragent-core/internal/engine"
)

const gameTurnMaxBodyBytes = 4 * 1024

type gameTurnRequest struct {
	ChatID int64  `json:"chat_id"`
	Reason string `json:"reason,omitempty"`
}

// handleGameTurn is POST /api/game-turn, served on the same dedicated
// loopback-only listener as /api/game-event (reuses GAME_EVENT_TOKEN --
// no new secret). Called by services/game_watcher/sts2_poller.py whenever it
// detects the game has reached an actionable decision point while
// autonomous play is active. Deliberately does the minimum here: auth,
// active/busy checks, then hands off to engine.PublishGameTurn exactly like
// ClockEngine hands off to PublishProactiveTurn -- Go remains the sole
// originator of reasoning turns (see docs/ARCHITECTURE.md and this
// session's plan doc for why: a Python-originated turn that skipped
// StateThinking would have its CSM transitions silently rejected by
// IsValidTransition, desyncing Go's per-chat state tracking from reality).
func (s *Server) handleGameTurn(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	if s.gameEventToken == "" {
		writeGameEventJSON(w, http.StatusServiceUnavailable, map[string]interface{}{"error": "game turn ingestion disabled"})
		return
	}

	suppliedToken := r.Header.Get("X-Game-Event-Token")
	if subtle.ConstantTimeCompare([]byte(suppliedToken), []byte(s.gameEventToken)) != 1 {
		s.logger.Warn("Rejected game turn with invalid/missing token", zap.String("remote_addr", r.RemoteAddr))
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, gameTurnMaxBodyBytes)
	var req gameTurnRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			http.Error(w, `{"error":"request body too large"}`, http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
		return
	}

	if s.autonomousPlayState == nil || !s.autonomousPlayState.IsActive() {
		writeGameEventJSON(w, http.StatusOK, map[string]interface{}{"status": "inactive"})
		return
	}

	targetChatID := req.ChatID
	if s.autonomousPlayState != nil && s.autonomousPlayState.TargetChatID() != 0 {
		targetChatID = s.autonomousPlayState.TargetChatID()
	}

	if targetChatID == 0 {
		http.Error(w, `{"error":"chat_id is required"}`, http.StatusBadRequest)
		return
	}

	// Overlap-prevention debounce: reuse the CSM as the single source of
	// truth for "a turn is already in flight" instead of inventing new
	// cross-process state -- if the previous game turn hasn't returned to
	// IDLE yet, skip this poll's request rather than firing a second
	// concurrent turn for the same chat.
	currentState := s.bridge.csm.GetChatState(targetChatID)
	if s.bridge == nil || s.bridge.csm == nil || currentState != engine.StateIdle {
		writeGameEventJSON(w, http.StatusOK, map[string]interface{}{"status": "busy"})
		return
	}

	s.logger.Info("🎮 Game turn triggered", zap.Int64("chat_id", targetChatID), zap.String("reason", req.Reason))

	engine.PublishGameTurn(s.bridge.bus, s.bridge.csm, s.bridge.emotionalState, s.bridge.personality, s.bridge.circadian, targetChatID, s.logger)

	writeGameEventJSON(w, http.StatusOK, map[string]interface{}{"status": "ok"})
}
