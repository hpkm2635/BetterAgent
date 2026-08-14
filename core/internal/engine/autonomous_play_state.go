package engine

import "sync"

// AutonomousPlayState is a single global on/off toggle for autonomous game
// play (e.g. Slay the Spire 2 via services/game_watcher/sts2_poller.go's
// /api/game-turn hook). Deliberately a single global, not a per-chat map --
// this MVP assumes one human plays one local game at a time, matching the
// STS2 mod's own single local-instance design. If BetterAgent ever needs to
// host multiple simultaneous autonomous-play sessions, this is the type to
// generalize into a map[int64]bool.
//
// Default OFF: nothing fires until a human explicitly activates it (see
// "/game_start" handling in gotd/adapter.go and webgateway/nats_bridge.go).
type AutonomousPlayState struct {
	mu     sync.Mutex
	active bool
	chatID int64
}

func NewAutonomousPlayState() *AutonomousPlayState {
	return &AutonomousPlayState{}
}

func (s *AutonomousPlayState) IsActive() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.active
}

func (s *AutonomousPlayState) Activate(chatID int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.active = true
	s.chatID = chatID
}

// Deactivate turns autonomous play off and returns the chat_id that was
// active (0 if it wasn't active), so callers can publish a stream-cancel for
// that specific chat as part of the emergency-stop sequence.
func (s *AutonomousPlayState) Deactivate() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	chatID := s.chatID
	if !s.active {
		return 0
	}
	s.active = false
	s.chatID = 0
	return chatID
}

func (s *AutonomousPlayState) TargetChatID() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.chatID
}
