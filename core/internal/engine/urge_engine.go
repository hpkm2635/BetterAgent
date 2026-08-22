package engine

import (
	"math"
	"sync"
	"time"

	"go.uber.org/zap"

	"betteragent-core/internal/emotion"
)

// UrgeParams configures UrgeEngine's accumulation/threshold/cooldown
// behavior. All duration fields are already resolved from config seconds by
// the caller (see cmd/main.go).
type UrgeParams struct {
	AlphaBoredom         float64
	BetaGameEvent        float64
	GammaUnreadPressure  float64
	BaseThreshold        float64
	ArousalSensitivity   float64
	EnergyPenalty        float64
	MinThreshold         float64
	MaxThreshold         float64
	UrgeCap              float64
	CooldownDuration     time.Duration
	DeadZoneDuration     time.Duration
	GameEventDecay       time.Duration
	UnreadPressureWindow time.Duration
	PrimaryChatID        int64
	TargetSessionMaxAge  time.Duration
}

const defaultProactiveReason = "一段时间没有人跟你说话，你觉得有点无聊，想主动搭个话"

// UrgeEngine accumulates a global "urge to speak" impulse, integrated once
// per ClockEngine tick from boredom + decaying game-event spikes + a proxy
// for unread-chat pressure, and fires a proactive turn once the accumulator
// crosses a mood-dependent dynamic threshold. A single global accumulator
// mirrors the existing global EmotionalState singleton (see
// docs/ARCHITECTURE.md) -- the catgirl has one "want to talk" impulse, not
// one per chat.
type UrgeEngine struct {
	mu sync.Mutex

	params UrgeParams

	value                float64
	gameEventEnergy      float64
	cooldownUntil        time.Time
	deadZoneUntil        time.Time
	consecutiveUnreplied int
	lastReason           string

	logger *zap.Logger
}

func NewUrgeEngine(params UrgeParams, logger *zap.Logger) *UrgeEngine {
	return &UrgeEngine{
		params: params,
		logger: logger,
	}
}

// CurrentValue returns a snapshot of the current Urge accumulator, mainly
// for diagnostic responses (see webgateway/game_event_handler.go).
func (u *UrgeEngine) CurrentValue() float64 {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.value
}

func (u *UrgeEngine) PrimaryChatID() int64                { return u.params.PrimaryChatID }
func (u *UrgeEngine) TargetMaxAge() time.Duration         { return u.params.TargetSessionMaxAge }
func (u *UrgeEngine) UnreadPressureWindow() time.Duration { return u.params.UnreadPressureWindow }

// RecordGameEvent adds weight to the decaying "hot" game-event contribution
// and remembers reason as the proactive-reason text to use if this spike is
// what pushes Urge over threshold. Called in-process by the game-event HTTP
// handler -- not round-tripped through NATS, so it stays correct even if the
// bus is offline.
func (u *UrgeEngine) RecordGameEvent(weight float64, reason string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.gameEventEnergy += weight
	if reason != "" {
		u.lastReason = reason
	}
	u.logger.Debug("UrgeEngine recorded game event",
		zap.Float64("weight", weight),
		zap.Float64("game_event_energy", u.gameEventEnergy),
	)
}

// OnUserActivity resets the consecutive unreplied proactive counter when the
// user interacts/sends a message, returning proactive threshold to normal.
func (u *UrgeEngine) OnUserActivity() {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.consecutiveUnreplied > 0 {
		u.logger.Info("UrgeEngine: User activity received, resetting consecutive unreplied counter to 0",
			zap.Int("previous_unreplied", u.consecutiveUnreplied),
		)
		u.consecutiveUnreplied = 0
	}
}

// ConsecutiveUnreplied returns current unreplied proactive count (diagnostic/test).
func (u *UrgeEngine) ConsecutiveUnreplied() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.consecutiveUnreplied
}

// OnTurnCompleted resets Urge to zero and enforces the cooldown/dead-zone
// refractory period. Called on every completed turn (proactive or not) so
// the catgirl never becomes a repeat-chatterbox regardless of what
// triggered the turn that just finished.
func (u *UrgeEngine) OnTurnCompleted() {
	u.mu.Lock()
	defer u.mu.Unlock()
	now := time.Now()
	u.value = 0
	u.gameEventEnergy = 0
	u.lastReason = ""
	if u.params.CooldownDuration > 0 {
		u.cooldownUntil = now.Add(u.params.CooldownDuration)
	}
	if u.params.DeadZoneDuration > 0 {
		u.deadZoneUntil = now.Add(u.params.DeadZoneDuration)
	}
}

// EvaluateTick integrates one tick's worth of Urge and decides whether to
// fire a proactive turn. Must be called after emotionalState.ApplyTimeDecay
// and stateMachine.EvaluateTick so it reads post-decay mood and
// post-transition FSM state.
func (u *UrgeEngine) EvaluateTick(
	now time.Time,
	elapsed time.Duration,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	isSleepHours bool,
	targetState State,
	unreadPressure int,
) (shouldFire bool, reason string) {
	u.mu.Lock()
	defer u.mu.Unlock()

	elapsedSeconds := elapsed.Seconds()
	if elapsedSeconds < 0 {
		elapsedSeconds = 0
	}

	// 1. Decay the "hot" game-event contribution first.
	if u.params.GameEventDecay > 0 && u.gameEventEnergy != 0 {
		decayFactor := math.Exp(-elapsedSeconds / u.params.GameEventDecay.Seconds())
		u.gameEventEnergy *= decayFactor
	}

	// 2. Boredom term -- Extraversion is the existing PersonalityProfile
	// field documented as "high = proactive"; this is the first place it
	// actually does anything.
	extraversion := 0.5
	if personality != nil {
		extraversion = personality.Extraversion
	}
	boredomRate := 0.6 + 0.4*extraversion

	// 3. Integrate.
	integrand := u.params.AlphaBoredom*boredomRate +
		u.params.BetaGameEvent*u.gameEventEnergy +
		u.params.GammaUnreadPressure*float64(unreadPressure)
	u.value += integrand * elapsedSeconds
	u.value = clamp(u.value, 0, u.params.UrgeCap)

	// 4. Hard gates -- never interrupt an ongoing turn, speak during sleep
	// hours, or fire again inside the post-speech cooldown/dead-zone.
	// cooldownUntil is the longer refractory window (config: cooldown_seconds,
	// e.g. 90s); deadZoneUntil is the short one (dead_zone_seconds, e.g. 20s)
	// -- both are set together by OnTurnCompleted, so both must be checked
	// here or the shorter one silently wins regardless of how the longer one
	// is configured.
	if now.Before(u.cooldownUntil) || now.Before(u.deadZoneUntil) || isSleepHours || targetState != StateIdle {
		return false, ""
	}

	// 5. Dynamic threshold from mood: high arousal lowers it (talks more),
	// low energy raises it (talks less).
	arousal, energy := 0.5, 0.5
	if emoState != nil {
		arousal = emoState.GetArousal()
		energy = emoState.GetEnergy()
	}
	threshold := u.params.BaseThreshold *
		(1 - u.params.ArousalSensitivity*(arousal-0.5)) *
		(1 + u.params.EnergyPenalty*(1-energy))

	// 6. Exponential backoff for unreplied proactive turns:
	// If master does not reply to proactive chatter(s), scale threshold up
	// (1.0x -> 1.8x -> 3.24x -> 5.0x cap), reducing proactive chatter frequency.
	if u.consecutiveUnreplied > 0 {
		backoffFactor := math.Pow(1.8, float64(u.consecutiveUnreplied))
		if backoffFactor > 5.0 {
			backoffFactor = 5.0
		}
		threshold *= backoffFactor
	}

	threshold = clamp(threshold, u.params.MinThreshold, u.params.MaxThreshold*5.0)

	if u.value < threshold {
		return false, ""
	}

	reason = u.lastReason
	if reason == "" {
		reason = defaultProactiveReason
	}
	u.value = 0
	u.gameEventEnergy = 0
	u.lastReason = ""
	u.consecutiveUnreplied++
	u.logger.Info("UrgeEngine proactive turn triggered",
		zap.Int("consecutive_unreplied", u.consecutiveUnreplied),
		zap.String("reason", reason),
	)
	return true, reason
}

func clamp(v, min, max float64) float64 {
	if v < min {
		return min
	}
	if v > max {
		return max
	}
	return v
}

// ResolveProactiveTarget picks which chat a proactive turn should be aimed
// at: a pinned primaryChatID if configured, otherwise the most recently
// active chat (within maxAge), otherwise none.
func ResolveProactiveTarget(csm *CentralStateMachine, primaryChatID int64, maxAge time.Duration) (int64, bool) {
	if primaryChatID != 0 {
		return primaryChatID, true
	}
	return csm.GetMostRecentlyActiveChatID(maxAge)
}
