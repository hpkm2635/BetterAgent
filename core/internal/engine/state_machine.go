package engine

import (
	"fmt"
	"sync"
	"time"

	"go.uber.org/zap"

	"betteragent-core/internal/emotion"
)

type State string

const (
	StateIdle            State = "IDLE"
	StateThinking        State = "THINKING"
	StateExecutingAction State = "EXECUTING_ACTION"
	StateErrorRecovery   State = "ERROR_RECOVERY"
	StateSleeping        State = "SLEEPING"
	StateMoodyRest       State = "MOODY_REST"
)

const (
	ThinkingTimeoutDuration        = 45 * time.Second
	ExecutingActionTimeoutDuration = 45 * time.Second
)

// IsValidTransition enforces state machine transition rules from ARCHITECTURE.md
func IsValidTransition(from, to State) bool {
	if from == to {
		return true
	}
	switch from {
	case StateIdle:
		return to == StateThinking || to == StateSleeping || to == StateMoodyRest
	case StateThinking:
		return to == StateExecutingAction || to == StateIdle || to == StateErrorRecovery
	case StateExecutingAction:
		return to == StateIdle || to == StateErrorRecovery
	case StateSleeping:
		return to == StateThinking || to == StateIdle
	case StateMoodyRest:
		return to == StateIdle || to == StateThinking
	case StateErrorRecovery:
		return to == StateIdle
	default:
		return true
	}
}

type ChatStateMachine struct {
	mu             sync.Mutex
	chatID         int64
	currentState   State
	lastTransition time.Time
	watchdogTimer  *time.Timer
	logger         *zap.Logger
	onTimeout      func(chatID int64, state State)
}

func newChatStateMachine(chatID int64, logger *zap.Logger, onTimeout func(chatID int64, state State)) *ChatStateMachine {
	return &ChatStateMachine{
		chatID:         chatID,
		currentState:   StateIdle,
		lastTransition: time.Now(),
		logger:         logger,
		onTimeout:      onTimeout,
	}
}

func (csm *ChatStateMachine) GetState() State {
	csm.mu.Lock()
	defer csm.mu.Unlock()
	return csm.currentState
}

func (csm *ChatStateMachine) resetWatchdog(newState State) {
	if csm.watchdogTimer != nil {
		csm.watchdogTimer.Stop()
		csm.watchdogTimer = nil
	}

	var timeoutDuration time.Duration
	if newState == StateThinking {
		timeoutDuration = ThinkingTimeoutDuration
	} else if newState == StateExecutingAction {
		timeoutDuration = ExecutingActionTimeoutDuration
	}

	if timeoutDuration > 0 {
		targetState := newState
		csm.watchdogTimer = time.AfterFunc(timeoutDuration, func() {
			csm.logger.Warn("💥 Deadman Switch Watchdog Fired! Chat state stuck, forcing recovery to IDLE",
				zap.Int64("chat_id", csm.chatID),
				zap.String("stuck_state", string(targetState)),
				zap.Duration("timeout", timeoutDuration),
			)
			csm.mu.Lock()
			csm.currentState = StateIdle
			csm.lastTransition = time.Now()
			csm.mu.Unlock()

			if csm.onTimeout != nil {
				csm.onTimeout(csm.chatID, targetState)
			}
		})
	}
}

func (csm *ChatStateMachine) TransitionTo(newState State, reason string) bool {
	csm.mu.Lock()
	defer csm.mu.Unlock()

	if csm.currentState == newState {
		return true
	}

	if !IsValidTransition(csm.currentState, newState) {
		csm.logger.Warn("Invalid state transition attempt rejected",
			zap.Int64("chat_id", csm.chatID),
			zap.String("from", string(csm.currentState)),
			zap.String("to", string(newState)),
			zap.String("reason", reason),
		)
		// Force transition if error recovery
		if newState != StateErrorRecovery && newState != StateIdle {
			return false
		}
	}

	csm.logger.Info("Chat State Machine Transition",
		zap.Int64("chat_id", csm.chatID),
		zap.String("from", string(csm.currentState)),
		zap.String("to", string(newState)),
		zap.String("reason", reason),
	)

	csm.currentState = newState
	csm.lastTransition = time.Now()
	csm.resetWatchdog(newState)
	return true
}

type CentralStateMachine struct {
	mu            sync.RWMutex
	chatStates    map[int64]*ChatStateMachine
	logger        *zap.Logger
	timeoutCb     func(chatID int64, state State)
}

func NewCentralStateMachine(logger *zap.Logger) *CentralStateMachine {
	return &CentralStateMachine{
		chatStates: make(map[int64]*ChatStateMachine),
		logger:     logger,
	}
}

func (sm *CentralStateMachine) SetTimeoutCallback(cb func(chatID int64, state State)) {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	sm.timeoutCb = cb
}

func (sm *CentralStateMachine) getOrCreateChatSM(chatID int64) *ChatStateMachine {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	csm, exists := sm.chatStates[chatID]
	if !exists {
		csm = newChatStateMachine(chatID, sm.logger, sm.timeoutCb)
		sm.chatStates[chatID] = csm
	}
	return csm
}

func (sm *CentralStateMachine) GetCurrentState() State {
	return sm.GetChatState(0)
}

func (sm *CentralStateMachine) GetChatState(chatID int64) State {
	return sm.getOrCreateChatSM(chatID).GetState()
}

func (sm *CentralStateMachine) TransitionTo(newState State, reason string) bool {
	return sm.TransitionToChat(0, newState, reason)
}

func (sm *CentralStateMachine) TransitionToChat(chatID int64, newState State, reason string) bool {
	return sm.getOrCreateChatSM(chatID).TransitionTo(newState, reason)
}

func (sm *CentralStateMachine) EvaluateTick(isSleepHours bool, emotionSig *emotion.EventSignal) {
	sm.mu.RLock()
	defer sm.mu.RUnlock()

	for _, csm := range sm.chatStates {
		csm.mu.Lock()
		if emotionSig != nil {
			if *emotionSig == emotion.SignalGoodnight && csm.currentState != StateSleeping {
				csm.currentState = StateSleeping
				csm.lastTransition = time.Now()
				csm.mu.Unlock()
				continue
			}
			if *emotionSig == emotion.SignalEmotionEvent && csm.currentState == StateIdle {
				csm.currentState = StateMoodyRest
				csm.lastTransition = time.Now()
				csm.mu.Unlock()
				continue
			}
		}

		if isSleepHours && csm.currentState == StateIdle {
			csm.currentState = StateSleeping
			csm.lastTransition = time.Now()
		} else if !isSleepHours && csm.currentState == StateSleeping {
			csm.currentState = StateIdle
			csm.lastTransition = time.Now()
		}
		csm.mu.Unlock()
	}
}

func (sm *CentralStateMachine) String() string {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	return fmt.Sprintf("CentralStateMachine{active_chats: %d}", len(sm.chatStates))
}
