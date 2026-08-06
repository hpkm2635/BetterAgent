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

type TypingHeartbeatManager struct {
	sender TypingSender
	logger *zap.Logger
	tasks  map[int64]context.CancelFunc
	mu     sync.Mutex
}

func NewTypingHeartbeatManager(sender TypingSender, logger *zap.Logger) *TypingHeartbeatManager {
	return &TypingHeartbeatManager{
		sender: sender,
		logger: logger,
		tasks:  make(map[int64]context.CancelFunc),
	}
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
		if err := m.sender.SendTypingAction(ctx, chatID, action); err != nil {
			m.logger.Debug("Initial typing action send error", zap.Int64("chat_id", chatID), zap.Error(err))
		}

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := m.sender.SendTypingAction(ctx, chatID, action); err != nil {
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
