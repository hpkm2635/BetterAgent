package gotd

import (
	"context"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

// peerLimiterIdleTTL/peerLimiterPruneEvery bound the growth of peerLimiters:
// entries unused for peerLimiterIdleTTL are swept out lazily on the write
// path (no extra goroutine), gated by peerLimiterPruneEvery so a sweep isn't
// attempted on every single call. Evicting a limiter just resets that peer's
// burst allowance back to full -- harmless, not a correctness issue.
const (
	peerLimiterIdleTTL    = 30 * time.Minute
	peerLimiterPruneEvery = 5 * time.Minute
)

type peerLimiterEntry struct {
	limiter  *rate.Limiter
	lastUsed time.Time
}

type AntiSpamGuard struct {
	globalLimiter *rate.Limiter
	peerLimiters  map[int64]*peerLimiterEntry
	mu            sync.Mutex
	lastPrune     time.Time
}

func NewAntiSpamGuard() *AntiSpamGuard {
	// Global: max 20 messages per second, burst of 5
	return &AntiSpamGuard{
		globalLimiter: rate.NewLimiter(rate.Limit(20), 5),
		peerLimiters:  make(map[int64]*peerLimiterEntry),
	}
}

func (g *AntiSpamGuard) Wait(ctx context.Context, peerID int64) error {
	if err := g.globalLimiter.Wait(ctx); err != nil {
		return err
	}

	limiter := g.getPeerLimiter(peerID)
	return limiter.Wait(ctx)
}

func (g *AntiSpamGuard) getPeerLimiter(peerID int64) *rate.Limiter {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.pruneLocked()

	entry, exists := g.peerLimiters[peerID]
	if !exists {
		// Per-peer: max 1 message per second, burst 2
		entry = &peerLimiterEntry{limiter: rate.NewLimiter(rate.Every(1*time.Second), 2)}
		g.peerLimiters[peerID] = entry
	}
	entry.lastUsed = time.Now()
	return entry.limiter
}

// pruneLocked sweeps out idle peer limiters. Caller must hold g.mu.
func (g *AntiSpamGuard) pruneLocked() {
	now := time.Now()
	if now.Sub(g.lastPrune) < peerLimiterPruneEvery {
		return
	}
	g.lastPrune = now

	for peerID, entry := range g.peerLimiters {
		if now.Sub(entry.lastUsed) > peerLimiterIdleTTL {
			delete(g.peerLimiters, peerID)
		}
	}
}
