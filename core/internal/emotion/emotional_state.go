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
	mu sync.RWMutex `json:"-"`

	// VAD 3D space
	Valence   float64 `json:"valence"`   // [-1.0, 1.0]
	Arousal   float64 `json:"arousal"`   // [0.0, 1.0]
	Dominance float64 `json:"dominance"` // [0.0, 1.0]

	// Physiological metrics
	Energy        float64 `json:"energy"`         // [0.0, 1.0]
	Satiety       float64 `json:"satiety"`        // [0.0, 1.0]
	SocialBattery float64 `json:"social_battery"` // [0.0, 1.0]

	// Extended fields
	AffectionLevel  float64   `json:"affection_level"` // [0.0, 100.0]
	CurrentMoodTag  MoodEnum  `json:"current_mood_tag"`
	JealousyLevel   float64   `json:"jealousy_level"` // [0.0, 1.0]
	BaselineValence float64   `json:"baseline_valence"`
	LastUpdated     time.Time `json:"last_updated"`
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
		JealousyLevel:   0.0,
		BaselineValence: 0.2, // Slightly cheerful base
		LastUpdated:     time.Now(),
	}
}

func (e *EmotionalState) DeepCopy() *EmotionalState {
	e.mu.RLock()
	defer e.mu.RUnlock()

	return &EmotionalState{
		Valence:         e.Valence,
		Arousal:         e.Arousal,
		Dominance:       e.Dominance,
		Energy:          e.Energy,
		Satiety:         e.Satiety,
		SocialBattery:   e.SocialBattery,
		AffectionLevel:  e.AffectionLevel,
		CurrentMoodTag:  e.CurrentMoodTag,
		JealousyLevel:   e.JealousyLevel,
		BaselineValence: e.BaselineValence,
		LastUpdated:     e.LastUpdated,
	}
}

func (e *EmotionalState) IsJealous() bool {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.JealousyLevel > 0.3
}

func (e *EmotionalState) SetJealousy(level float64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.JealousyLevel = math.Max(0.0, math.Min(1.0, level))
	e.updateMoodTagLocked()
}

func (e *EmotionalState) ApplySatietyDelta(dSatiety float64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.Satiety = math.Max(0.0, math.Min(1.0, e.Satiety+dSatiety))
	e.updateMoodTagLocked()
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
	// Satiety decays slowly over time
	e.Satiety = math.Max(0.0, e.Satiety-0.02*hours)
	// Jealousy level gradually decays over time
	e.JealousyLevel = math.Max(0.0, e.JealousyLevel-0.1*hours)

	e.LastUpdated = time.Now()
	e.updateMoodTagLocked()
}

func (e *EmotionalState) updateMoodTagLocked() {
	if e.JealousyLevel > 0.3 {
		e.CurrentMoodTag = MoodJealous
	} else if e.Energy < 0.2 {
		e.CurrentMoodTag = MoodSleepy
	} else if e.Valence < -0.3 || e.Satiety < 0.2 {
		e.CurrentMoodTag = MoodMoody
	} else if e.Valence > 0.4 {
		e.CurrentMoodTag = MoodHappy
	} else {
		e.CurrentMoodTag = MoodNeutral
	}
}

// GetArousal and GetEnergy are locked accessors for packages outside
// emotion (e.g. engine.UrgeEngine) that need to read VAD/physiological
// fields without reaching past the mutex.
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

func (e *EmotionalState) GetSocialBattery() float64 {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.SocialBattery
}

func (e *EmotionalState) CheckTrigger() *EventSignal {
	e.mu.RLock()
	defer e.mu.RUnlock()

	if e.Energy < 0.1 {
		sig := SignalGoodnight
		return &sig
	}
	if e.Valence < -0.6 || e.JealousyLevel > 0.3 {
		sig := SignalEmotionEvent
		return &sig
	}
	return nil
}

func (e *EmotionalState) ToPromptDescription() string {
	e.mu.RLock()
	defer e.mu.RUnlock()

	isJealous := e.JealousyLevel > 0.3
	return fmt.Sprintf(
		"[猫娘内心状态] 当前心情: %s (愉悦度: %.2f, 激动度: %.2f, 亲密度: %.1f, 精力: %.2f, 饱腹感: %.2f, 社交电量: %.2f, 吃醋中: %v)",
		e.CurrentMoodTag, e.Valence, e.Arousal, e.AffectionLevel, e.Energy, e.Satiety, e.SocialBattery, isJealous,
	)
}

