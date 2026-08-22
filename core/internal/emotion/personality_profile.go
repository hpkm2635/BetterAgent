package emotion

import "fmt"

type PersonalityProfile struct {
	TsundereLevel     float64 `json:"tsundere_level"`     // 0.0 - 1.0 (high = cold outside, warm inside)
	Clinginess        float64 `json:"clinginess"`         // 0.0 - 1.0 (high = wants attention)
	JealousyThreshold float64 `json:"jealousy_threshold"` // 0.0 - 1.0 (low = jealous easily)
	CatNature         float64 `json:"cat_nature"`         // 0.0 - 1.0 (high = aloof, fragmented)
	Neuroticism       float64 `json:"neuroticism"`         // 0.0 - 1.0 (high = mood swings)
	Extraversion      float64 `json:"extraversion"`        // 0.0 - 1.0 (high = proactive)
}

func DefaultPersonality() *PersonalityProfile {
	return &PersonalityProfile{
		TsundereLevel:     0.7,
		Clinginess:        0.8,
		JealousyThreshold: 0.4,
		CatNature:         0.6,
		Neuroticism:       0.5,
		Extraversion:      0.7,
	}
}

func NewPersonalityFromConfig(cfg map[string]interface{}) *PersonalityProfile {
	p := DefaultPersonality()
	if cfg == nil {
		return p
	}
	getFloat := func(key string, fallback float64) float64 {
		if val, ok := cfg[key]; ok {
			switch v := val.(type) {
			case float64:
				return v
			case float32:
				return float64(v)
			case int:
				return float64(v)
			case int64:
				return float64(v)
			}
		}
		return fallback
	}

	p.TsundereLevel = getFloat("tsundere_level", p.TsundereLevel)
	p.Clinginess = getFloat("clinginess", p.Clinginess)
	p.JealousyThreshold = getFloat("jealousy_threshold", p.JealousyThreshold)
	p.CatNature = getFloat("cat_nature", p.CatNature)
	p.Neuroticism = getFloat("neuroticism", p.Neuroticism)
	p.Extraversion = getFloat("extraversion", p.Extraversion)
	return p
}


func (p *PersonalityProfile) ModifySentimentDelta(dV, dA, dAff float64) (float64, float64, float64) {
	// Neuroticism amplifies negative deltas
	if dV < 0 {
		dV *= (1.0 + p.Neuroticism*0.5)
	}
	// Tsundere slightly dampens positive affection gains (acts stubborn)
	if dAff > 0 {
		dAff *= (1.0 - p.TsundereLevel*0.3)
	}
	return dV, dA, dAff
}

func (p *PersonalityProfile) ToPromptDescription() string {
	return fmt.Sprintf(
		"[猫娘性格设定] 傲娇度: %.1f, 粘人度: %.1f, 猫性: %.1f, 嫉妒敏感度: %.1f",
		p.TsundereLevel, p.Clinginess, p.CatNature, 1.0-p.JealousyThreshold,
	)
}
