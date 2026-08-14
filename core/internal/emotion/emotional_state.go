package emotion

import (
	"fmt"
	"math"
	"sync"
	"time"
)

type MoodEnum string

const (
	MoodHappy   MoodEnum = "HAPPY"
	MoodNeutral MoodEnum = "NEUTRAL"
	MoodMoody   MoodEnum = "MOODY"
	MoodSleepy  MoodEnum = "SLEEPY"
	MoodJealous MoodEnum = "JEALOUS"
)

type EventSignal string

const (
	SignalEmotionEvent EventSignal = "EMOTION_EVENT"
	SignalGoodnight    EventSignal = "GOODNIGHT_EVENT"
)

type EmotionalState struct {
	mu sync.RWMutex

	// VAD 3D space
	Valence   float64 // [-1.0, 1.0]
	Arousal   float64 // [0.0, 1.0]
	Dominance float64 // [0.0, 1.0]

	// Physiological metrics
	Energy        float64 // [0.0, 1.0]
	Satiety       float64 // [0.0, 1.0]
	SocialBattery float64 // [0.0, 1.0]

	// Extended fields
	AffectionLevel  float64 // [0.0, 100.0]
	CurrentMoodTag  MoodEnum
	IsJealous       bool
	BaselineValence float64
	LastUpdated     time.Time
}

func NewEmotionalState() *EmotionalState {
	return &EmotionalState{
		Valence:         0.5,
		Arousal:         0.5,
		Dominance:       0.5,
		Energy:          0.8,
		Satiety:         0.7,
		SocialBattery:   0.9,
		AffectionLevel:  50.0,
		CurrentMoodTag:  MoodNeutral,
		IsJealous:       false,
		BaselineValence: 0.2, // Slightly cheerful base
		LastUpdated:     time.Now(),
	}
}

func (e *EmotionalState) ApplySentimentDelta(dValence, dArousal, dAffection float64) {
	e.mu.Lock()
	defer e.mu.Unlock()

	e.Valence = math.Max(-1.0, math.Min(1.0, e.Valence+dValence))
	e.Arousal = math.Max(0.0, math.Min(1.0, e.Arousal+dArousal))
	e.AffectionLevel = math.Max(0.0, math.Min(100.0, e.AffectionLevel+dAffection))
	e.SocialBattery = math.Max(0.0, e.SocialBattery-0.02) // Each message consumes a bit of social battery
	e.LastUpdated = time.Now()

	e.updateMoodTagLocked()
}

func (e *EmotionalState) ApplyTimeDecay(elapsed time.Duration, circadianFactor float64) {
	e.mu.Lock()
	defer e.mu.Unlock()

	hours := elapsed.Hours()
	decayRate := 0.05 * hours

	// Valence naturally drifts back to BaselineValence
	if e.Valence > e.BaselineValence {
		e.Valence = math.Max(e.BaselineValence, e.Valence-decayRate)
	} else if e.Valence < e.BaselineValence {
		e.Valence = math.Min(e.BaselineValence, e.Valence+decayRate)
	}

	// Energy decays over time, influenced by circadian factor
	e.Energy = math.Max(0.0, math.Min(1.0, e.Energy-decayRate*0.1+circadianFactor*0.01))
	// Social battery recovers when idle
	e.SocialBattery = math.Min(1.0, e.SocialBattery+decayRate*0.2)

	e.LastUpdated = time.Now()
	e.updateMoodTagLocked()
}

func (e *EmotionalState) updateMoodTagLocked() {
	if e.IsJealous {
		e.CurrentMoodTag = MoodJealous
	} else if e.Energy < 0.2 {
		e.CurrentMoodTag = MoodSleepy
	} else if e.Valence < -0.3 {
		e.CurrentMoodTag = MoodMoody
	} else if e.Valence > 0.4 {
		e.CurrentMoodTag = MoodHappy
	} else {
		e.CurrentMoodTag = MoodNeutral
	}
}

// GetArousal and GetEnergy are locked accessors for packages outside
// emotion (e.g. engine.UrgeEngine) that need to read VAD/physiological
// fields without reaching past the mutex -- every other external read of
// EmotionalState goes through a locking method (ToPromptDescription,
// CheckTrigger), never a direct field access.
func (e *EmotionalState) GetArousal() float64 {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.Arousal
}

func (e *EmotionalState) GetEnergy() float64 {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.Energy
}

func (e *EmotionalState) CheckTrigger() *EventSignal {
	e.mu.RLock()
	defer e.mu.RUnlock()

	if e.Energy < 0.1 {
		sig := SignalGoodnight
		return &sig
	}
	if e.Valence < -0.6 || e.IsJealous {
		sig := SignalEmotionEvent
		return &sig
	}
	return nil
}

func (e *EmotionalState) ToPromptDescription() string {
	e.mu.RLock()
	defer e.mu.RUnlock()

	return fmt.Sprintf(
		"[猫娘内心状态] 当前心情: %s (愉悦度: %.2f, 激动度: %.2f, 亲密度: %.1f, 精力: %.2f, 社交电量: %.2f, 吃醋中: %v)",
		e.CurrentMoodTag, e.Valence, e.Arousal, e.AffectionLevel, e.Energy, e.SocialBattery, e.IsJealous,
	)
}
