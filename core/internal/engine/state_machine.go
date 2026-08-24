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
	StateListening       State = "LISTENING"
	StateStreamingSTT    State = "STREAMING_STT"
	StateThinking        State = "THINKING"
	StateTalking         State = "TALKING"
	StateStreamingTTS    State = "STREAMING_TTS"
	StateInterrupted     State = "INTERRUPTED"
	StateCancelling      State = "CANCELLING"
	StateExecutingAction State = "EXECUTING_ACTION"
	StateErrorRecovery   State = "ERROR_RECOVERY"
	StateSleeping        State = "SLEEPING"
	StateMoodyRest       State = "MOODY_REST"
)

const (
	ListeningTimeoutDuration       = 60 * time.Second
	StreamingSTTTimeoutDuration    = 30 * time.Second
	ThinkingTimeoutDuration        = 45 * time.Second
	TalkingTimeoutDuration         = 60 * time.Second
	StreamingTTSTimeoutDuration    = 30 * time.Second
	CancellingTimeoutDuration      = 10 * time.Second
	ExecutingActionTimeoutDuration = 45 * time.Second

	// ChatStateInactivityTTL bounds how long an IDLE per-chat state machine is
	// kept around before CentralStateMachine.PruneInactive evicts it. Prevents
	// unbounded growth of chatStates from chats/sessions that never come back
	// (e.g. throwaway WebGateway sessions). Never evicts a chat mid-conversation.
	ChatStateInactivityTTL = 2 * time.Hour
)

// IsValidTransition enforces state machine transition rules for Digital Human & Multimodal Agent
func IsValidTransition(from, to State) bool {
	if from == to {
		return true
	}
	switch from {
	case StateIdle:
		return to == StateThinking || to == StateListening || to == StateStreamingSTT || to == StateStreamingTTS || to == StateSleeping || to == StateMoodyRest
	case StateListening:
		return to == StateStreamingSTT || to == StateThinking || to == StateIdle || to == StateInterrupted || to == StateCancelling
	case StateStreamingSTT:
		return to == StateThinking || to == StateIdle || to == StateCancelling || to == StateInterrupted
	case StateThinking:
		return to == StateTalking || to == StateStreamingTTS || to == StateExecutingAction || to == StateIdle || to == StateErrorRecovery || to == StateInterrupted || to == StateCancelling
	case StateTalking:
		return to == StateStreamingTTS || to == StateIdle || to == StateInterrupted || to == StateCancelling || to == StateExecutingAction || to == StateThinking
	case StateStreamingTTS:
		return to == StateIdle || to == StateInterrupted || to == StateCancelling || to == StateThinking
	case StateInterrupted:
		return to == StateCancelling || to == StateThinking || to == StateIdle
	case StateCancelling:
		// StateListening: barge-in case -- agent was talking, user's speech_start
		// fires the shared cancel-and-listen path (see nats_bridge.go), landing
		// here from StateCancelling moments before the VAD-driven Listening
		// transition is attempted. Without this, that Listening attempt would
		// be rejected as invalid whenever it races the cancel.
		return to == StateIdle || to == StateThinking || to == StateErrorRecovery || to == StateListening
	case StateExecutingAction:
		return to == StateIdle || to == StateErrorRecovery || to == StateTalking || to == StateStreamingTTS
	case StateSleeping:
		return to == StateThinking || to == StateIdle || to == StateListening
	case StateMoodyRest:
		return to == StateIdle || to == StateThinking || to == StateListening
	case StateErrorRecovery:
		return to == StateIdle
	default:
		return false
	}
}

type ChatStateMachine struct {
	mu             sync.Mutex
	chatID         int64
	currentState   State
	generationID   uint64
	lastTransition time.Time
	lastTouchTime  time.Time
	watchdogTimer  *time.Timer
	logger         *zap.Logger
	onTimeout      func(chatID int64, state State)
}

func newChatStateMachine(chatID int64, logger *zap.Logger, onTimeout func(chatID int64, state State)) *ChatStateMachine {
	return &ChatStateMachine{
		chatID:         chatID,
		currentState:   StateIdle,
		generationID:   1,
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

func (csm *ChatStateMachine) GetGeneration() uint64 {
	csm.mu.Lock()
	defer csm.mu.Unlock()
	return csm.generationID
}

func (csm *ChatStateMachine) IncrementGeneration() uint64 {
	csm.mu.Lock()
	defer csm.mu.Unlock()
	csm.generationID++
	return csm.generationID
}

// TouchWatchdog refreshes/extends the watchdog timer window for streaming states with 2s sliding debounce
func (csm *ChatStateMachine) TouchWatchdog(timeoutDuration time.Duration) {
	csm.mu.Lock()
	defer csm.mu.Unlock()

	// Debounce: Skip expensive timer recreate if touched within last 2 seconds
	if time.Since(csm.lastTouchTime) < 2*time.Second {
		return
	}
	csm.lastTouchTime = time.Now()

	if csm.watchdogTimer != nil {
		csm.watchdogTimer.Stop()
		csm.watchdogTimer = nil
	}

	if timeoutDuration <= 0 {
		timeoutDuration = 15 * time.Second
	}

	targetState := csm.currentState
	csm.watchdogTimer = time.AfterFunc(timeoutDuration, func() {
		csm.mu.Lock()
		// 🛡️ Double Check Guard: If state has already changed from targetState (e.g. returned to IDLE), cancel watchdog trigger!
		if csm.currentState != targetState {
			csm.mu.Unlock()
			return
		}
		csm.currentState = StateIdle
		csm.lastTransition = time.Now()
		csm.watchdogTimer = nil
		csm.mu.Unlock()

		if targetState == StateStreamingTTS || targetState == StateTalking {
			csm.logger.Info("Stream audio speech completed, state smoothly recovered to IDLE",
				zap.Int64("chat_id", csm.chatID),
				zap.String("previous_state", string(targetState)),
				zap.Duration("idle_timeout", timeoutDuration),
			)
		} else {
			csm.logger.Warn("💥 Stream Watchdog Timeout (No Response Received)! Forcing recovery to IDLE",
				zap.Int64("chat_id", csm.chatID),
				zap.String("stuck_state", string(targetState)),
				zap.Duration("timeout", timeoutDuration),
			)
		}

		if csm.onTimeout != nil {
			csm.onTimeout(csm.chatID, targetState)
		}
	})
}

func (csm *ChatStateMachine) resetWatchdog(newState State) {
	if csm.watchdogTimer != nil {
		csm.watchdogTimer.Stop()
		csm.watchdogTimer = nil
	}

	var timeoutDuration time.Duration
	switch newState {
	case StateThinking:
		timeoutDuration = ThinkingTimeoutDuration
	case StateTalking:
		timeoutDuration = TalkingTimeoutDuration
	case StateStreamingTTS:
		timeoutDuration = StreamingTTSTimeoutDuration
	case StateListening:
		timeoutDuration = ListeningTimeoutDuration
	case StateStreamingSTT:
		timeoutDuration = StreamingSTTTimeoutDuration
	case StateCancelling:
		timeoutDuration = CancellingTimeoutDuration
	case StateExecutingAction:
		timeoutDuration = ExecutingActionTimeoutDuration
	}

	if timeoutDuration > 0 {
		targetState := newState
		csm.watchdogTimer = time.AfterFunc(timeoutDuration, func() {
			csm.mu.Lock()
			// 🛡️ Double Check Guard: If state has already changed from targetState (e.g. returned to IDLE), cancel watchdog trigger!
			if csm.currentState != targetState {
				csm.mu.Unlock()
				return
			}
			csm.currentState = StateIdle
			csm.lastTransition = time.Now()
			csm.watchdogTimer = nil
			csm.mu.Unlock()

			csm.logger.Warn("💥 Deadman Switch Watchdog Fired! Chat state stuck, forcing recovery to IDLE",
				zap.Int64("chat_id", csm.chatID),
				zap.String("stuck_state", string(targetState)),
				zap.Duration("timeout", timeoutDuration),
			)

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
		// Force transition ONLY if error recovery
		if newState != StateErrorRecovery {
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
	mu               sync.RWMutex
	chatStates       map[int64]*ChatStateMachine
	logger           *zap.Logger
	timeoutCb        func(chatID int64, state State)
	lastActiveChatID int64
	lastActiveAt     time.Time
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
	// Dynamically refresh onTimeout callback across all active ChatStateMachine instances
	for _, csm := range sm.chatStates {
		csm.mu.Lock()
		csm.onTimeout = cb
		csm.mu.Unlock()
	}
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

// PruneInactive evicts chat state machines that are IDLE and have been
// untouched for longer than maxIdle. It never evicts a chat mid-conversation
// (only IDLE is eligible), so eviction is always safe: if the chat becomes
// active again, getOrCreateChatSM transparently recreates a fresh IDLE entry.
// Returns the number of entries evicted.
func (sm *CentralStateMachine) PruneInactive(maxIdle time.Duration) int {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	pruned := 0
	now := time.Now()
	for chatID, csm := range sm.chatStates {
		csm.mu.Lock()
		idle := csm.currentState == StateIdle && now.Sub(csm.lastTransition) > maxIdle
		if idle && csm.watchdogTimer != nil {
			csm.watchdogTimer.Stop()
			csm.watchdogTimer = nil
		}
		csm.mu.Unlock()

		if idle {
			delete(sm.chatStates, chatID)
			pruned++
		}
	}
	return pruned
}

// TouchActivity records chatID as the most recently active chat, used by
// UrgeEngine (via ResolveProactiveTarget) to pick which session a proactive
// turn should be aimed at when no PrimaryChatID is configured.
func (sm *CentralStateMachine) TouchActivity(chatID int64) {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	sm.lastActiveChatID = chatID
	sm.lastActiveAt = time.Now()
}

// GetMostRecentlyActiveChatID returns the last chat touched via
// TouchActivity, provided that touch happened within maxAge. Returns
// (0, false) if no chat has been touched yet or the most recent touch is
// stale.
func (sm *CentralStateMachine) GetMostRecentlyActiveChatID(maxAge time.Duration) (int64, bool) {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	if sm.lastActiveChatID == 0 {
		return 0, false
	}
	if maxAge > 0 && time.Since(sm.lastActiveAt) > maxAge {
		return 0, false
	}
	return sm.lastActiveChatID, true
}

// CountRecentlyActiveChatsExcluding counts chats other than chatID whose
// last state transition happened within window. Used as the UnreadChatPressure
// proxy in UrgeEngine -- an honest placeholder for "how many other
// conversations are currently active," not a true unread-message backlog
// (this codebase has no such concept today).
func (sm *CentralStateMachine) CountRecentlyActiveChatsExcluding(chatID int64, window time.Duration) int {
	sm.mu.RLock()
	defer sm.mu.RUnlock()

	now := time.Now()
	count := 0
	for id, csm := range sm.chatStates {
		if id == chatID {
			continue
		}
		csm.mu.Lock()
		recent := now.Sub(csm.lastTransition) <= window
		csm.mu.Unlock()
		if recent {
			count++
		}
	}
	return count
}

func (sm *CentralStateMachine) GetChatState(chatID int64) State {
	return sm.getOrCreateChatSM(chatID).GetState()
}

func (sm *CentralStateMachine) TransitionToChat(chatID int64, newState State, reason string) bool {
	return sm.getOrCreateChatSM(chatID).TransitionTo(newState, reason)
}

func (sm *CentralStateMachine) GetGenerationChat(chatID int64) uint64 {
	return sm.getOrCreateChatSM(chatID).GetGeneration()
}

func (sm *CentralStateMachine) IncrementGenerationChat(chatID int64) uint64 {
	return sm.getOrCreateChatSM(chatID).IncrementGeneration()
}

func (sm *CentralStateMachine) TouchWatchdogChat(chatID int64, timeout time.Duration) {
	sm.getOrCreateChatSM(chatID).TouchWatchdog(timeout)
}

func (sm *CentralStateMachine) EvaluateTick(isSleepHours bool, emotionSig *emotion.EventSignal) {
	sm.mu.RLock()
	chats := make([]*ChatStateMachine, 0, len(sm.chatStates))
	for _, csm := range sm.chatStates {
		chats = append(chats, csm)
	}
	sm.mu.RUnlock()

	for _, csm := range chats {
		currentState := csm.GetState()
		if emotionSig != nil {
			if *emotionSig == emotion.SignalGoodnight && currentState != StateSleeping {
				csm.TransitionTo(StateSleeping, "goodnight_signal")
				continue
			}
			if *emotionSig == emotion.SignalEmotionEvent && currentState == StateIdle {
				csm.TransitionTo(StateMoodyRest, "emotion_event")
				continue
			}
		}

		if isSleepHours && currentState == StateIdle {
			csm.TransitionTo(StateSleeping, "circadian_sleep_hours")
		} else if !isSleepHours && currentState == StateSleeping {
			csm.TransitionTo(StateIdle, "circadian_wake_hours")
		}
	}
}

func (sm *CentralStateMachine) String() string {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	return fmt.Sprintf("CentralStateMachine{active_chats: %d}", len(sm.chatStates))
}
