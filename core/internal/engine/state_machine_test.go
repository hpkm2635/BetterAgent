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
