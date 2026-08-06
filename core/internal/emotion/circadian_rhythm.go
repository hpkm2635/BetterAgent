package emotion

import (
	"fmt"
	"time"
)

type CircadianRhythmEvaluator struct {
	DayStartHour   int // default 8
	NightStartHour int // default 23
}

func NewCircadianRhythmEvaluator() *CircadianRhythmEvaluator {
	return &CircadianRhythmEvaluator{
		DayStartHour:   8,
		NightStartHour: 23,
	}
}

func (c *CircadianRhythmEvaluator) GetCircadianFactor(hour int) float64 {
	if hour >= c.DayStartHour && hour < 12 {
		return 1.0 // Peak morning energy
	} else if hour >= 12 && hour < 18 {
		return 0.9 // Afternoon steady
	} else if hour >= 18 && hour < c.NightStartHour {
		return 0.7 // Evening relaxing
	} else {
		return 0.2 // Late night sleepy
	}
}

func (c *CircadianRhythmEvaluator) IsSleepHours(hour int) bool {
	return hour >= c.NightStartHour || hour < c.DayStartHour
}

func (c *CircadianRhythmEvaluator) ToPromptDescription() string {
	now := time.Now()
	hour := now.Hour()
	factor := c.GetCircadianFactor(hour)
	timeStr := now.Format("15:04")

	phase := "白天"
	if c.IsSleepHours(hour) {
		phase = "深夜/休息时段"
	}

	return fmt.Sprintf("[作息时间] 当前本地时间: %s (%s, 生理活力指数: %.1f)", timeStr, phase, factor)
}
