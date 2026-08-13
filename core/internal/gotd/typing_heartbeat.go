package gotd

import (
	"context"
	"sync"
	"time"

	"go.uber.org/zap"
)

type TypingSender interface {
	SendTypingAction(ctx context.Context, chatID int64, action string) error
}

// RateLimiter is satisfied by *AntiSpamGuard. Typing status calls previously
// bypassed anti-spam throttling entirely -- StartHeartbeat's own guards (one
// call per inbound message, 4s ticker) make that an unlikely trigger on
// their own, but there's no reason this path should be the one gap in an
// otherwise-guarded send pipeline.
type RateLimiter interface {
	Wait(ctx context.Context, peerID int64) error
}

type TypingHeartbeatManager struct {
	sender  TypingSender
	limiter RateLimiter
	logger  *zap.Logger
	tasks   map[int64]context.CancelFunc
	mu      sync.Mutex
}

func NewTypingHeartbeatManager(sender TypingSender, limiter RateLimiter, logger *zap.Logger) *TypingHeartbeatManager {
	return &TypingHeartbeatManager{
		sender:  sender,
		limiter: limiter,
		logger:  logger,
		tasks:   make(map[int64]context.CancelFunc),
	}
}

func (m *TypingHeartbeatManager) sendThrottled(ctx context.Context, chatID int64, action string) error {
	if m.limiter != nil {
		if err := m.limiter.Wait(ctx, chatID); err != nil {
			return err
		}
	}
	return m.sender.SendTypingAction(ctx, chatID, action)
}

func (m *TypingHeartbeatManager) StartHeartbeat(chatID int64, action string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Stop existing task if any
	if cancel, exists := m.tasks[chatID]; exists {
		cancel()
	}

	ctx, cancel := context.WithCancel(context.Background())
	m.tasks[chatID] = cancel

	go func() {
		ticker := time.NewTicker(4 * time.Second)
		defer ticker.Stop()

		// Send initial action
		if err := m.sendThrottled(ctx, chatID, action); err != nil {
			m.logger.Debug("Initial typing action send error", zap.Int64("chat_id", chatID), zap.Error(err))
		}

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := m.sendThrottled(ctx, chatID, action); err != nil {
					m.logger.Debug("Heartbeat typing action error", zap.Int64("chat_id", chatID), zap.Error(err))
				}
			}
		}
	}()
}

func (m *TypingHeartbeatManager) StopHeartbeat(chatID int64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if cancel, exists := m.tasks[chatID]; exists {
		cancel()
		delete(m.tasks, chatID)
	}
}
