package engine

import (
	"testing"
	"time"

	"go.uber.org/zap"
)

func TestPruneInactive_EvictsOnlyIdleAndStaleChats(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())

	// Chat 1: IDLE and stale -> should be pruned.
	staleIdle := sm.getOrCreateChatSM(1)
	staleIdle.mu.Lock()
	staleIdle.currentState = StateIdle
	staleIdle.lastTransition = time.Now().Add(-3 * time.Hour)
	staleIdle.mu.Unlock()

	// Chat 2: IDLE but recent -> must survive.
	freshIdle := sm.getOrCreateChatSM(2)
	freshIdle.mu.Lock()
	freshIdle.currentState = StateIdle
	freshIdle.lastTransition = time.Now()
	freshIdle.mu.Unlock()

	// Chat 3: mid-conversation (THINKING) and stale by wall-clock -> must never be evicted.
	activeStale := sm.getOrCreateChatSM(3)
	activeStale.mu.Lock()
	activeStale.currentState = StateThinking
	activeStale.lastTransition = time.Now().Add(-3 * time.Hour)
	activeStale.mu.Unlock()

	pruned := sm.PruneInactive(1 * time.Hour)
	if pruned != 1 {
		t.Fatalf("expected exactly 1 chat pruned, got %d", pruned)
	}

	sm.mu.RLock()
	defer sm.mu.RUnlock()

	if _, exists := sm.chatStates[1]; exists {
		t.Errorf("expected stale IDLE chat 1 to be evicted, but it still exists")
	}
	if _, exists := sm.chatStates[2]; !exists {
		t.Errorf("expected fresh IDLE chat 2 to survive, but it was evicted")
	}
	if _, exists := sm.chatStates[3]; !exists {
		t.Errorf("expected active (THINKING) chat 3 to survive regardless of age, but it was evicted")
	}
}

func TestIsValidTransition_CancellingToListeningAllowsBargeInThenListen(t *testing.T) {
	// Barge-in case: agent is talking, user's speech_start fires the shared
	// cancel-and-listen path, landing on StateCancelling moments before the
	// VAD-driven Listening transition is attempted. See nats_bridge.go.
	if !IsValidTransition(StateCancelling, StateListening) {
		t.Errorf("expected StateCancelling -> StateListening to be a valid transition (barge-in then listen)")
	}
}

func TestIsValidTransition_CancellingStillRejectsUnrelatedStates(t *testing.T) {
	// The new StateListening destination must not have loosened this into
	// accepting arbitrary transitions -- StateTalking was never valid from
	// StateCancelling and still shouldn't be.
	if IsValidTransition(StateCancelling, StateTalking) {
		t.Errorf("expected StateCancelling -> StateTalking to remain invalid")
	}
}

func TestPruneInactive_RecreatesTransparentlyAfterEviction(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())

	csm := sm.getOrCreateChatSM(42)
	csm.mu.Lock()
	csm.currentState = StateIdle
	csm.lastTransition = time.Now().Add(-1 * time.Hour)
	csm.mu.Unlock()

	if pruned := sm.PruneInactive(30 * time.Minute); pruned != 1 {
		t.Fatalf("expected 1 chat pruned, got %d", pruned)
	}

	// A later message for the same chatID must transparently get a fresh IDLE machine.
	state := sm.GetChatState(42)
	if state != StateIdle {
		t.Errorf("expected recreated chat to start IDLE, got %s", state)
	}
}

func TestGetMostRecentlyActiveChatID_ReturnsLastTouched(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())

	sm.TouchActivity(1)
	sm.TouchActivity(2)
	sm.TouchActivity(3)

	chatID, ok := sm.GetMostRecentlyActiveChatID(time.Hour)
	if !ok || chatID != 3 {
		t.Errorf("expected most recently touched chat 3, got chatID=%d ok=%v", chatID, ok)
	}
}

func TestGetMostRecentlyActiveChatID_RespectsMaxAge(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())

	sm.mu.Lock()
	sm.lastActiveChatID = 5
	sm.lastActiveAt = time.Now().Add(-2 * time.Hour)
	sm.mu.Unlock()

	if _, ok := sm.GetMostRecentlyActiveChatID(1 * time.Hour); ok {
		t.Errorf("expected stale activity (2h old) to be rejected by a 1h maxAge")
	}

	if chatID, ok := sm.GetMostRecentlyActiveChatID(0); !ok || chatID != 5 {
		t.Errorf("expected maxAge=0 to mean 'no age limit', got chatID=%d ok=%v", chatID, ok)
	}
}

func TestGetMostRecentlyActiveChatID_NoneWhenNeverTouched(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())
	if _, ok := sm.GetMostRecentlyActiveChatID(time.Hour); ok {
		t.Errorf("expected no result when TouchActivity was never called")
	}
}

func TestCountRecentlyActiveChatsExcluding(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())

	recent := sm.getOrCreateChatSM(1)
	recent.mu.Lock()
	recent.lastTransition = time.Now()
	recent.mu.Unlock()

	alsoRecent := sm.getOrCreateChatSM(2)
	alsoRecent.mu.Lock()
	alsoRecent.lastTransition = time.Now()
	alsoRecent.mu.Unlock()

	stale := sm.getOrCreateChatSM(3)
	stale.mu.Lock()
	stale.lastTransition = time.Now().Add(-1 * time.Hour)
	stale.mu.Unlock()

	// The target chat itself (2) must never be counted even though it's recent.
	count := sm.CountRecentlyActiveChatsExcluding(2, 5*time.Minute)
	if count != 1 {
		t.Errorf("expected exactly 1 other recently active chat (chat 1), got %d", count)
	}
}
