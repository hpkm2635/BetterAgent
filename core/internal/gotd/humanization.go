package gotd

import (
	"math/rand"
	"time"
)

type HumanizationEngine struct {
	baseReadingSpeedCharsPerSec float64
}

func NewHumanizationEngine() *HumanizationEngine {
	return &HumanizationEngine{
		baseReadingSpeedCharsPerSec: 25.0, // ~1500 chars/min
	}
}

func (h *HumanizationEngine) CalculateDelay(text string, mediaType *string) time.Duration {
	charCount := len([]rune(text))
	baseSeconds := float64(charCount) * 0.04 // ~0.04s per character

	if baseSeconds < 0.5 {
		baseSeconds = 0.5
	}
	if baseSeconds > 8.0 {
		baseSeconds = 8.0
	}

	// Add media overhead
	if mediaType != nil {
		switch *mediaType {
		case "photo":
			baseSeconds += 1.5
		case "voice":
			baseSeconds += 2.0
		}
	}

	// Add random jitter +/- 15%
	jitter := (rand.Float64()*0.3 - 0.15) * baseSeconds
	finalSeconds := baseSeconds + jitter

	return time.Duration(finalSeconds * float64(time.Second))
}
