package webgateway

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"

	"go.uber.org/zap"
)

const gameStateMaxBodyBytes = 4 * 1024

type gameStateRequest struct {
	ChatID int64 `json:"chat_id"`
	Floor  int   `json:"floor"`
	HP     int   `json:"hp"`
	MaxHP  int   `json:"max_hp"`
	Gold   int   `json:"gold"`
	Act    int   `json:"act"`
}

type AgentGameStatePayload struct {
	Floor int `json:"floor"`
	HP    int `json:"hp"`
	MaxHP int `json:"max_hp"`
	Gold  int `json:"gold"`
	Act   int `json:"act"`
}

// handleGameState is POST /api/game-state, served on the loopback-only game-event
// listener. Called by sts2_poller.py whenever game snapshot stats change.
// Marshals an agent.game_state WSMessage and broadcasts it directly to the active web session.
func (s *Server) handleGameState(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	if s.gameEventToken == "" {
		writeGameEventJSON(w, http.StatusServiceUnavailable, map[string]interface{}{"error": "game event ingestion disabled"})
		return
	}

	suppliedToken := r.Header.Get("X-Game-Event-Token")
	if subtle.ConstantTimeCompare([]byte(suppliedToken), []byte(s.gameEventToken)) != 1 {
		s.logger.Warn("Rejected game state with invalid/missing token", zap.String("remote_addr", r.RemoteAddr))
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, gameStateMaxBodyBytes)
	var req gameStateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			http.Error(w, `{"error":"request body too large"}`, http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
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

	if s.bridge != nil && s.bridge.sessions != nil {
		payloadBytes, _ := json.Marshal(AgentGameStatePayload{
			Floor: req.Floor,
			HP:    req.HP,
			MaxHP: req.MaxHP,
			Gold:  req.Gold,
			Act:   req.Act,
		})

		outBytes, _ := json.Marshal(WSMessage{
			Type:    "agent.game_state",
			Payload: payloadBytes,
		})

		s.bridge.sessions.SendTextToChat(targetChatID, outBytes)
	}

	writeGameEventJSON(w, http.StatusOK, map[string]interface{}{"status": "ok"})
}
