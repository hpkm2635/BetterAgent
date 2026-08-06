package gotd

import (
	"context"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

type AntiSpamGuard struct {
	globalLimiter *rate.Limiter
	peerLimiters  map[int64]*rate.Limiter
	mu            sync.Mutex
}

func NewAntiSpamGuard() *AntiSpamGuard {
	// Global: max 20 messages per second, burst of 5
	return &AntiSpamGuard{
		globalLimiter: rate.NewLimiter(rate.Limit(20), 5),
		peerLimiters:  make(map[int64]*rate.Limiter),
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

	limiter, exists := g.peerLimiters[peerID]
	if !exists {
		// Per-peer: max 1 message per second, burst 2
		limiter = rate.NewLimiter(rate.Every(1*time.Second), 2)
		g.peerLimiters[peerID] = limiter
	}
	return limiter
}
