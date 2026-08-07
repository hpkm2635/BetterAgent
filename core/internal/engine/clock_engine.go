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
	ticker       *time.Ticker
	bus          *bus.NatsBus
	stateMachine *CentralStateMachine
	emotionalState *emotion.EmotionalState
	circadian    *emotion.CircadianRhythmEvaluator
	logger       *zap.Logger
	counter      int
	lastTickTime time.Time
}

func NewClockEngine(
	interval time.Duration,
	natsBus *bus.NatsBus,
	csm *CentralStateMachine,
	emoState *emotion.EmotionalState,
	circadian *emotion.CircadianRhythmEvaluator,
	logger *zap.Logger,
) *ClockEngine {
	return &ClockEngine{
		ticker:       time.NewTicker(interval),
		bus:          natsBus,
		stateMachine: csm,
		emotionalState: emoState,
		circadian:    circadian,
		logger:       logger,
		counter:      0,
		lastTickTime: time.Now(),
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
