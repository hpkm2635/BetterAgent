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
	ticker         *time.Ticker
	bus            *bus.NatsBus
	stateMachine   *CentralStateMachine
	emotionalState *emotion.EmotionalState
	personality    *emotion.PersonalityProfile
	circadian      *emotion.CircadianRhythmEvaluator
	urgeEngine          *UrgeEngine
	autonomousPlayState *AutonomousPlayState
	logger              *zap.Logger
	counter             int
	lastTickTime        time.Time
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
	return &ClockEngine{
		ticker:              time.NewTicker(interval),
		bus:                 natsBus,
		stateMachine:        csm,
		emotionalState:      emoState,
		personality:         personality,
		circadian:           circadian,
		urgeEngine:          urgeEngine,
		autonomousPlayState: autonomousPlayState,
		logger:              logger,
		counter:             0,
		lastTickTime:        time.Now(),
	}
}

func (ce *ClockEngine) Start(ctx context.Context) {
	ce.logger.Info("ClockEngine started")
	go func() {
		for {
			select {
			case <-ctx.Done():
				ce.ticker.Stop()
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

	// Apply time decay to EmotionalState
	ce.emotionalState.ApplyTimeDecay(elapsed, circadianFactor)

	// Check if emotion triggers a state machine event
	emotionSig := ce.emotionalState.CheckTrigger()
	ce.stateMachine.EvaluateTick(isSleepHours, emotionSig)

	// Bound chatStates map growth: evict IDLE chats that have been inactive
	// for a while (e.g. one-off WebGateway sessions that never return).
	if pruned := ce.stateMachine.PruneInactive(ChatStateInactivityTTL); pruned > 0 {
		ce.logger.Debug("Pruned inactive chat state machines", zap.Int("pruned", pruned))
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
			if fire, reason := ce.urgeEngine.EvaluateTick(now, elapsed, ce.emotionalState, ce.personality, isSleepHours, targetState, unreadPressure); fire {
				PublishProactiveTurn(ce.bus, ce.stateMachine, ce.emotionalState, ce.personality, ce.circadian, targetChatID, reason, ce.logger)
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

	payload := schema.TickPayload{
		BasePayload:         schema.NewBasePayload("clock_engine"),
		ISOTime:             now.Format(time.RFC3339),
		TimeOfDay:           timeOfDay,
		IdleDurationSeconds: elapsed.Seconds(),
		IsSleepHours:        isSleepHours,
		TickCounter:         ce.counter,
		EmotionDescription:  ce.emotionalState.ToPromptDescription(),
	}

	if err := ce.bus.Publish(bus.SubjectTick, "clock_engine", payload); err != nil {
		ce.logger.Error("Failed to publish tick payload", zap.Error(err))
	} else {
		ce.logger.Debug("Tick published", zap.Int("counter", ce.counter), zap.String("state", string(ce.stateMachine.GetChatState(0))))
	}
}
