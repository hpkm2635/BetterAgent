package engine

import (
	"testing"
	"time"

	"go.uber.org/zap"

	"betteragent-core/internal/emotion"
)

func testUrgeParams() UrgeParams {
	return UrgeParams{
		AlphaBoredom:         1.0,
		BetaGameEvent:        1.0,
		GammaUnreadPressure:  0.5,
		BaseThreshold:        10.0,
		ArousalSensitivity:   0.6,
		EnergyPenalty:        0.5,
		MinThreshold:         1.0,
		MaxThreshold:         30.0,
		UrgeCap:              1000.0,
		CooldownDuration:     10 * time.Second,
		DeadZoneDuration:     5 * time.Second,
		GameEventDecay:       10 * time.Second,
		UnreadPressureWindow: 60 * time.Second,
	}
}

func neutralEmotion() *emotion.EmotionalState {
	e := emotion.NewEmotionalState()
	e.Arousal = 0.5
	e.Energy = 0.5
	return e
}

func TestUrgeEngine_AccumulatesMonotonicallyOverTicks(t *testing.T) {
	params := testUrgeParams()
	params.BaseThreshold = 1e9 // effectively unreachable, isolate accumulation from firing
	params.MaxThreshold = 1e9
	u := NewUrgeEngine(params, zap.NewNop())

	emo := neutralEmotion()
	personality := emotion.DefaultPersonality()

	prev := 0.0
	now := time.Now()
	for i := 0; i < 5; i++ {
		now = now.Add(1 * time.Second)
		_, _ = u.EvaluateTick(now, 1*time.Second, emo, personality, false, StateIdle, 0)
		cur := u.CurrentValue()
		if cur <= prev {
			t.Fatalf("expected Urge to increase monotonically, tick %d: prev=%f cur=%f", i, prev, cur)
		}
		prev = cur
	}
}

func TestUrgeEngine_ExtraversionIncreasesBoredomRate(t *testing.T) {
	now := time.Now()
	elapsed := 5 * time.Second
	emo := neutralEmotion()

	lowExtra := &emotion.PersonalityProfile{Extraversion: 0.0}
	highExtra := &emotion.PersonalityProfile{Extraversion: 1.0}

	params := testUrgeParams()
	params.BaseThreshold = 1e9
	params.MaxThreshold = 1e9

	uLow := NewUrgeEngine(params, zap.NewNop())
	uHigh := NewUrgeEngine(params, zap.NewNop())

	uLow.EvaluateTick(now, elapsed, emo, lowExtra, false, StateIdle, 0)
	uHigh.EvaluateTick(now, elapsed, emo, highExtra, false, StateIdle, 0)

	if uHigh.CurrentValue() <= uLow.CurrentValue() {
		t.Errorf("expected higher Extraversion to accumulate Urge faster: low=%f high=%f", uLow.CurrentValue(), uHigh.CurrentValue())
	}
}

func TestUrgeEngine_DynamicThreshold_HighArousalFiresSooner(t *testing.T) {
	// Same accumulated Urge, different mood -- high arousal should have a
	// lower threshold and thus fire while low arousal (higher threshold)
	// does not.
	params := testUrgeParams()
	params.BaseThreshold = 10.0
	params.ArousalSensitivity = 0.6
	params.EnergyPenalty = 0.0
	params.AlphaBoredom = 100.0 // fast accumulation so a single big tick crosses the low-arousal-side threshold band
	params.MinThreshold = 1.0
	params.MaxThreshold = 30.0

	personality := emotion.DefaultPersonality()
	now := time.Now()

	highArousal := emotion.NewEmotionalState()
	highArousal.Arousal = 1.0
	highArousal.Energy = 0.5

	lowArousal := emotion.NewEmotionalState()
	lowArousal.Arousal = 0.0
	lowArousal.Energy = 0.5

	uHigh := NewUrgeEngine(params, zap.NewNop())
	uLow := NewUrgeEngine(params, zap.NewNop())

	// One tick that lands between the two thresholds: high-arousal threshold
	// is lower (10 * (1 - 0.6*0.5) = 7), low-arousal threshold is higher
	// (10 * (1 - 0.6*-0.5) = 13). Accumulate ~10 in one tick.
	elapsed := 100 * time.Millisecond
	fireHigh, _ := uHigh.EvaluateTick(now, elapsed, highArousal, personality, false, StateIdle, 0)
	fireLow, _ := uLow.EvaluateTick(now, elapsed, lowArousal, personality, false, StateIdle, 0)

	if !fireHigh {
		t.Errorf("expected high-arousal (lower threshold) to fire, got Urge=%f", uHigh.CurrentValue())
	}
	if fireLow {
		t.Errorf("expected low-arousal (higher threshold) to NOT fire yet, got Urge=%f", uLow.CurrentValue())
	}
}

func TestUrgeEngine_DeadZoneSuppressesRefireThenReenables(t *testing.T) {
	params := testUrgeParams()
	params.BaseThreshold = 1.0
	params.MinThreshold = 1.0
	params.MaxThreshold = 1.0
	params.ArousalSensitivity = 0
	params.EnergyPenalty = 0
	params.AlphaBoredom = 10.0
	params.DeadZoneDuration = 200 * time.Millisecond
	params.CooldownDuration = 0 // isolate dead-zone gating from cooldown gating

	u := NewUrgeEngine(params, zap.NewNop())
	emo := neutralEmotion()
	personality := emotion.DefaultPersonality()

	// Simulate a turn having just completed (arms the dead zone).
	u.OnTurnCompleted()

	now := time.Now()
	fire, _ := u.EvaluateTick(now, 1*time.Second, emo, personality, false, StateIdle, 0)
	if fire {
		t.Fatalf("expected no fire while inside dead zone")
	}

	// Advance past the dead zone.
	now = now.Add(300 * time.Millisecond)
	fire, _ = u.EvaluateTick(now, 1*time.Second, emo, personality, false, StateIdle, 0)
	if !fire {
		t.Fatalf("expected fire once dead zone has elapsed and threshold is met, Urge=%f", u.CurrentValue())
	}
}

// TestUrgeEngine_CooldownGatesIndependentlyOfDeadZone is a regression test
// for a bug where OnTurnCompleted set cooldownUntil but EvaluateTick's gate
// only ever checked deadZoneUntil, making cooldown_seconds silently
// ineffective whenever it was longer than dead_zone_seconds (the configured
// default has cooldown=90s > dead_zone=20s) -- the catgirl could re-fire as
// soon as the much shorter dead zone elapsed, ignoring the configured
// cooldown entirely.
func TestUrgeEngine_CooldownGatesIndependentlyOfDeadZone(t *testing.T) {
	params := testUrgeParams()
	params.BaseThreshold = 1.0
	params.MinThreshold = 1.0
	params.MaxThreshold = 1.0
	params.ArousalSensitivity = 0
	params.EnergyPenalty = 0
	params.AlphaBoredom = 10.0
	params.DeadZoneDuration = 200 * time.Millisecond // short
	params.CooldownDuration = 2 * time.Second        // long -- must still gate after DeadZoneDuration elapses

	u := NewUrgeEngine(params, zap.NewNop())
	emo := neutralEmotion()
	personality := emotion.DefaultPersonality()

	u.OnTurnCompleted()

	now := time.Now()
	// Past the (short) dead zone, but well within the (long) cooldown.
	now = now.Add(500 * time.Millisecond)
	if fire, _ := u.EvaluateTick(now, 1*time.Second, emo, personality, false, StateIdle, 0); fire {
		t.Fatalf("expected no fire: dead zone has elapsed but cooldown (2s) has not")
	}

	// Past both.
	now = now.Add(2 * time.Second)
	if fire, _ := u.EvaluateTick(now, 1*time.Second, emo, personality, false, StateIdle, 0); !fire {
		t.Fatalf("expected fire once both cooldown and dead zone have elapsed, Urge=%f", u.CurrentValue())
	}
}

func TestUrgeEngine_NeverFiresOutsideIdleOrDuringSleepHours(t *testing.T) {
	params := testUrgeParams()
	params.BaseThreshold = 0.001
	params.MinThreshold = 0.001
	params.MaxThreshold = 0.001
	params.AlphaBoredom = 1000.0

	emo := neutralEmotion()
	personality := emotion.DefaultPersonality()
	now := time.Now()

	uBusy := NewUrgeEngine(params, zap.NewNop())
	if fire, _ := uBusy.EvaluateTick(now, 1*time.Second, emo, personality, false, StateThinking, 0); fire {
		t.Errorf("expected no fire while target chat is not IDLE")
	}

	uSleep := NewUrgeEngine(params, zap.NewNop())
	if fire, _ := uSleep.EvaluateTick(now, 1*time.Second, emo, personality, true, StateIdle, 0); fire {
		t.Errorf("expected no fire during sleep hours")
	}
}

func TestUrgeEngine_GameEventEnergyDecaysOverTicks(t *testing.T) {
	params := testUrgeParams()
	params.BaseThreshold = 1e9 // never fire, isolate accumulation from the firing reset
	params.MaxThreshold = 1e9
	params.AlphaBoredom = 0 // isolate game-event decay from boredom accumulation
	params.GammaUnreadPressure = 0
	params.BetaGameEvent = 1.0
	params.GameEventDecay = 10 * time.Second

	u := NewUrgeEngine(params, zap.NewNop())
	u.RecordGameEvent(100.0, "victory")

	emo := neutralEmotion()
	personality := emotion.DefaultPersonality()
	now := time.Now()

	// Equal-length ticks so each tick's marginal contribution (driven by the
	// decaying gameEventEnergy) should shrink monotonically tick over tick.
	tickElapsed := 1 * time.Second
	prevValue := 0.0
	prevIncrement := -1.0
	for i := 0; i < 5; i++ {
		now = now.Add(tickElapsed)
		_, _ = u.EvaluateTick(now, tickElapsed, emo, personality, false, StateIdle, 0)
		cur := u.CurrentValue()
		increment := cur - prevValue
		if i > 0 && increment >= prevIncrement {
			t.Errorf("tick %d: expected shrinking per-tick increment as game-event energy decays, prev_increment=%f increment=%f", i, prevIncrement, increment)
		}
		prevIncrement = increment
		prevValue = cur
	}
}

func TestUrgeEngine_OnTurnCompletedResetsValueAndGameEventEnergy(t *testing.T) {
	params := testUrgeParams()
	// Keep the threshold unreachable so EvaluateTick's own firing logic
	// (which also resets value/gameEventEnergy) doesn't fire and confound
	// what this test is actually checking: that OnTurnCompleted resets state
	// on its own, independent of a firing decision.
	params.BaseThreshold = 1e9
	params.MaxThreshold = 1e9
	u := NewUrgeEngine(params, zap.NewNop())
	u.RecordGameEvent(50.0, "rare_relic_pickup")

	emo := neutralEmotion()
	personality := emotion.DefaultPersonality()
	u.EvaluateTick(time.Now(), 1*time.Second, emo, personality, false, StateIdle, 0)

	if u.CurrentValue() == 0 {
		t.Fatalf("expected non-zero Urge before OnTurnCompleted for this test to be meaningful")
	}

	u.OnTurnCompleted()
	if u.CurrentValue() != 0 {
		t.Errorf("expected OnTurnCompleted to reset Urge to 0, got %f", u.CurrentValue())
	}
}

func TestResolveProactiveTarget_PrefersPrimaryChatID(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())
	sm.TouchActivity(999)

	chatID, ok := ResolveProactiveTarget(sm, 42, time.Hour)
	if !ok || chatID != 42 {
		t.Errorf("expected pinned primaryChatID 42 to win, got chatID=%d ok=%v", chatID, ok)
	}
}

func TestResolveProactiveTarget_FallsBackToMostRecentlyActive(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())
	sm.TouchActivity(7)

	chatID, ok := ResolveProactiveTarget(sm, 0, time.Hour)
	if !ok || chatID != 7 {
		t.Errorf("expected fallback to most recently active chat 7, got chatID=%d ok=%v", chatID, ok)
	}
}

func TestResolveProactiveTarget_NoneWhenNothingActive(t *testing.T) {
	sm := NewCentralStateMachine(zap.NewNop())

	_, ok := ResolveProactiveTarget(sm, 0, time.Hour)
	if ok {
		t.Errorf("expected no target when nothing has been touched")
	}
}

func TestUrgeEngine_UnrepliedBackoffDecreasesFrequency(t *testing.T) {
	params := testUrgeParams()
	params.BaseThreshold = 10.0
	params.MinThreshold = 1.0
	params.MaxThreshold = 100.0
	params.AlphaBoredom = 10.0
	params.CooldownDuration = 0
	params.DeadZoneDuration = 0

	u := NewUrgeEngine(params, zap.NewNop())
	emo := neutralEmotion()
	personality := emotion.DefaultPersonality()
	now := time.Now()

	// Boredom rate with default extraversion (0.5) is 0.8.
	// At AlphaBoredom=10.0, per-second integrand is 8.0.
	// 2 second tick accumulates 16.0, crossing BaseThreshold=10.0.
	fire1, _ := u.EvaluateTick(now, 2*time.Second, emo, personality, false, StateIdle, 0)
	if !fire1 {
		t.Fatalf("expected 1st proactive turn to fire")
	}
	if u.ConsecutiveUnreplied() != 1 {
		t.Errorf("expected consecutiveUnreplied=1, got %d", u.ConsecutiveUnreplied())
	}

	// Master does not reply. Second proactive turn threshold scaled up to 10 * 1.8 = 18.0!
	// A 1.5 second tick (accumulates 12.0) should NOT fire because 12.0 < 18.0.
	now = now.Add(2 * time.Second)
	fire2_short, _ := u.EvaluateTick(now, 1500*time.Millisecond, emo, personality, false, StateIdle, 0)
	if fire2_short {
		t.Errorf("expected 2nd proactive turn to NOT fire at short interval due to backoff multiplier")
	}

	// 3 second tick (accumulates 24.0) should cross backoff threshold 18.0 and fire!
	fire2, _ := u.EvaluateTick(now, 3*time.Second, emo, personality, false, StateIdle, 0)
	if !fire2 {
		t.Fatalf("expected 2nd proactive turn to fire after longer backoff interval")
	}
	if u.ConsecutiveUnreplied() != 2 {
		t.Errorf("expected consecutiveUnreplied=2, got %d", u.ConsecutiveUnreplied())
	}

	// User responds!
	u.OnUserActivity()
	if u.ConsecutiveUnreplied() != 0 {
		t.Errorf("expected consecutiveUnreplied=0 after user activity, got %d", u.ConsecutiveUnreplied())
	}

	// Next proactive turn returns to normal 10.0 threshold!
	now = now.Add(3 * time.Second)
	fireReset, _ := u.EvaluateTick(now, 2*time.Second, emo, personality, false, StateIdle, 0)
	if !fireReset {
		t.Fatalf("expected proactive turn to fire at normal threshold after user activity reset")
	}
}
