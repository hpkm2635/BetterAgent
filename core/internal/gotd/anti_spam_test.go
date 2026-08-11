package gotd

import (
	"testing"
	"time"
)

func TestAntiSpamGuard_PrunesIdlePeerLimiters(t *testing.T) {
	g := NewAntiSpamGuard()

	// Simulate a peer seen long ago.
	g.mu.Lock()
	g.peerLimiters[111] = &peerLimiterEntry{lastUsed: time.Now().Add(-2 * peerLimiterIdleTTL)}
	// Force the prune cooldown to have already elapsed.
	g.lastPrune = time.Now().Add(-2 * peerLimiterPruneEvery)
	g.mu.Unlock()

	// Any getPeerLimiter call re-checks the prune cooldown and sweeps.
	g.getPeerLimiter(222)

	g.mu.Lock()
	defer g.mu.Unlock()

	if _, exists := g.peerLimiters[111]; exists {
		t.Errorf("expected idle peer 111 to be pruned, but it still exists")
	}
	if _, exists := g.peerLimiters[222]; !exists {
		t.Errorf("expected freshly-touched peer 222 to exist after getPeerLimiter")
	}
}

func TestAntiSpamGuard_DoesNotPruneBeforeCooldown(t *testing.T) {
	g := NewAntiSpamGuard()

	g.mu.Lock()
	g.peerLimiters[111] = &peerLimiterEntry{lastUsed: time.Now().Add(-2 * peerLimiterIdleTTL)}
	g.lastPrune = time.Now() // cooldown just reset, sweep should be skipped
	g.mu.Unlock()

	g.getPeerLimiter(222)

	g.mu.Lock()
	defer g.mu.Unlock()

	if _, exists := g.peerLimiters[111]; !exists {
		t.Errorf("expected peer 111 to survive since prune cooldown had not elapsed yet")
	}
}
