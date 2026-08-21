package engine

import (
	"context"
	"time"

	"go.uber.org/zap"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/schema"
)

type ClockEngine struct {
	ticker              *time.Ticker
	bus                 *bus.NatsBus
	stateMachine        *CentralStateMachine
	emotionalState      *emotion.EmotionalState
	emotionStore        *emotion.EmotionalStateStore
	personality         *emotion.PersonalityProfile
	circadian           *emotion.CircadianRhythmEvaluator
	urgeEngine          *UrgeEngine
	autonomousPlayState *AutonomousPlayState
	logger              *zap.Logger
	counter             int
	lastTickTime        time.Time
	saveFilePath        string
}

func NewClockEngine(
	interval time.Duration,
	natsBus *bus.NatsBus,
	csm *CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	urgeEngine *UrgeEngine,
	autonomousPlayState *AutonomousPlayState,
	logger *zap.Logger,
) *ClockEngine {
	store := emotion.NewEmotionalStateStore(personality)
	return &ClockEngine{
		ticker:              time.NewTicker(interval),
		bus:                 natsBus,
		stateMachine:        csm,
		emotionalState:      emoState,
		emotionStore:        store,
		personality:         personality,
		circadian:           circadian,
		urgeEngine:          urgeEngine,
		autonomousPlayState: autonomousPlayState,
		logger:              logger,
		counter:             0,
		lastTickTime:        time.Now(),
		saveFilePath:        "data/emotion_states.json",
	}
}

func (ce *ClockEngine) SetEmotionStore(store *emotion.EmotionalStateStore) {
	if store != nil {
		ce.emotionStore = store
	}
}

func (ce *ClockEngine) GetEmotionStore() *emotion.EmotionalStateStore {
	return ce.emotionStore
}

func (ce *ClockEngine) SetSaveFilePath(path string) {
	if path != "" {
		ce.saveFilePath = path
	}
}

func (ce *ClockEngine) Start(ctx context.Context) {
	ce.logger.Info("ClockEngine started")

	// Try loading persisted states on start
	if ce.emotionStore != nil && ce.saveFilePath != "" {
		if err := ce.emotionStore.LoadFromFileWithRecovery(ce.saveFilePath); err != nil {
			ce.logger.Info("No previous emotion states loaded or load failed (will start fresh)", zap.Error(err))
		} else {
			ce.logger.Info("Successfully restored emotional states from disk", zap.String("path", ce.saveFilePath))
		}
	}

	go func() {
		for {
			select {
			case <-ctx.Done():
				ce.ticker.Stop()
				if ce.emotionStore != nil && ce.saveFilePath != "" {
					if err := ce.emotionStore.SaveToFileAtomic(ce.saveFilePath); err != nil {
						ce.logger.Error("Failed to save emotion states on shutdown", zap.Error(err))
					} else {
						ce.logger.Info("Saved emotion states on shutdown", zap.String("path", ce.saveFilePath))
					}
				}
				ce.logger.Info("ClockEngine stopped")
				return
			case t := <-ce.ticker.C:
				ce.onTick(t)
			}
		}
	}()
}

func (ce *ClockEngine) onTick(now time.Time) {
	ce.counter++
	elapsed := now.Sub(ce.lastTickTime)
	ce.lastTickTime = now

	hour := now.Hour()
	circadianFactor := ce.circadian.GetCircadianFactor(hour)
	isSleepHours := ce.circadian.IsSleepHours(hour)

	// Apply time decay to single emotionalState (backward compatibility)
	if ce.emotionalState != nil {
		ce.emotionalState.ApplyTimeDecay(elapsed, circadianFactor)
	}

	// Apply time decay across all stored chat emotional states
	if ce.emotionStore != nil {
		for _, chatID := range ce.emotionStore.GetAllActiveChatIDs() {
			if st, ok := ce.emotionStore.Get(chatID); ok {
				st.ApplyTimeDecay(elapsed, circadianFactor)
			}
		}
	}

	// Check if emotion triggers a state machine event
	var emotionSig *emotion.EventSignal
	if ce.emotionalState != nil {
		emotionSig = ce.emotionalState.CheckTrigger()
	}
	ce.stateMachine.EvaluateTick(isSleepHours, emotionSig)

	// Bound chatStates & emotionStore map growth: evict IDLE chats that have been inactive
	// for a while (e.g. one-off WebGateway sessions that never return).
	if pruned := ce.stateMachine.PruneInactive(ChatStateInactivityTTL); pruned > 0 {
		ce.logger.Debug("Pruned inactive chat state machines", zap.Int("pruned", pruned))
	}
	if ce.emotionStore != nil {
		if prunedEmo := ce.emotionStore.PruneInactive(ChatStateInactivityTTL); prunedEmo > 0 {
			ce.logger.Debug("Pruned inactive emotional state store entries", zap.Int("pruned", prunedEmo))
		}
	}

	// Periodically save emotion states atomically (e.g. every 10 ticks)
	if ce.emotionStore != nil && ce.counter%10 == 0 && ce.saveFilePath != "" {
		go func() {
			if err := ce.emotionStore.SaveToFileAtomic(ce.saveFilePath); err != nil {
				ce.logger.Error("Failed to auto-save emotion states", zap.Error(err))
			}
		}()
	}

	// Urge accumulation & proactive-speech decision. Must run after
	// EvaluateTick above so it observes any sleep/moody-rest transition that
	// just landed, and after PruneInactive so a just-evicted chat can't be
	// picked as the target. Suppressed during active autonomous game play.
	if ce.urgeEngine != nil {
		if ce.autonomousPlayState != nil && ce.autonomousPlayState.IsActive() {
			// Autonomous game play is active -- gameplay poller handles
			// game turns & game commentary. Suppress off-game casual chatter.
			ce.urgeEngine.OnTurnCompleted()
		} else if targetChatID, ok := ResolveProactiveTarget(ce.stateMachine, ce.urgeEngine.PrimaryChatID(), ce.urgeEngine.TargetMaxAge()); ok {
			targetState := ce.stateMachine.GetChatState(targetChatID)
			unreadPressure := ce.stateMachine.CountRecentlyActiveChatsExcluding(targetChatID, ce.urgeEngine.UnreadPressureWindow())

			// Use chat-specific EmotionalState if present in store, fallback to global
			targetEmo := ce.emotionalState
			if ce.emotionStore != nil {
				targetEmo = ce.emotionStore.GetOrCreate(targetChatID)
			}

			if fire, reason := ce.urgeEngine.EvaluateTick(now, elapsed, targetEmo, ce.personality, isSleepHours, targetState, unreadPressure); fire {
				PublishProactiveTurn(ce.bus, ce.stateMachine, targetEmo, ce.personality, ce.circadian, targetChatID, reason, ce.logger)
			}
		}
	}

	// Determine time of day string
	timeOfDay := "afternoon"
	if hour >= 6 && hour < 12 {
		timeOfDay = "morning"
	} else if hour >= 18 && hour < 22 {
		timeOfDay = "evening"
	} else if hour >= 22 || hour < 6 {
		timeOfDay = "night"
	}

	activeEmo := ce.emotionalState
	if activeEmo == nil && ce.emotionStore != nil {
		activeEmo = ce.emotionStore.GetOrCreate(0)
	}

	payload := schema.TickPayload{
		BasePayload:         schema.NewBasePayload("clock_engine"),
		ISOTime:             now.Format(time.RFC3339),
		TimeOfDay:           timeOfDay,
		IdleDurationSeconds: elapsed.Seconds(),
		IsSleepHours:        isSleepHours,
		TickCounter:         ce.counter,
		EmotionDescription:  activeEmo.ToPromptDescription(),
	}

	if err := ce.bus.Publish(bus.SubjectTick, "clock_engine", payload); err != nil {
		ce.logger.Error("Failed to publish tick payload", zap.Error(err))
	} else {
		ce.logger.Debug("Tick published", zap.Int("counter", ce.counter), zap.String("state", string(ce.stateMachine.GetChatState(0))))
	}
}
