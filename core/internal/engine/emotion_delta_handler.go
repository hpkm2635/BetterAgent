package engine

import (
	"encoding/json"
	"math"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/schema"
	"github.com/nats-io/nats.go"
	"go.uber.org/zap"
)

type EmotionDeltaHandler struct {
	bus          *bus.NatsBus
	emotionStore *emotion.EmotionalStateStore
	logger       *zap.Logger
}

func NewEmotionDeltaHandler(
	natsBus *bus.NatsBus,
	store *emotion.EmotionalStateStore,
	logger *zap.Logger,
) *EmotionDeltaHandler {
	return &EmotionDeltaHandler{
		bus:          natsBus,
		emotionStore: store,
		logger:       logger,
	}
}

func (h *EmotionDeltaHandler) Start() error {
	if h.bus == nil || h.emotionStore == nil {
		return nil
	}

	_, err := h.bus.Subscribe(bus.SubjectEmotionDelta, func(msg *nats.Msg) {
		var env struct {
			Payload schema.EmotionDeltaPayload `json:"payload"`
		}
		if err := json.Unmarshal(msg.Data, &env); err != nil {
			h.logger.Warn("Failed to unmarshal EmotionDeltaPayload", zap.Error(err))
			return
		}

		p := env.Payload
		if p.ChatID == 0 {
			return
		}

		// Clamp sanitization defense against prompt injection
		dV := math.Max(-0.3, math.Min(0.3, p.DeltaValence))
		dA := math.Max(-0.3, math.Min(0.3, p.DeltaArousal))
		dAff := math.Max(-2.0, math.Min(2.0, p.DeltaAffection))

		st := h.emotionStore.GetOrCreate(p.ChatID)
		st.ApplySentimentDelta(dV, dA, dAff)

		if p.IsJealous {
			st.SetJealousy(0.8)
		}

		h.logger.Info("Applied dynamic EmotionDelta from NATS",
			zap.Int64("chat_id", p.ChatID),
			zap.Float64("d_valence", dV),
			zap.Float64("d_arousal", dA),
			zap.Float64("d_affection", dAff),
			zap.Bool("is_jealous", p.IsJealous),
			zap.String("mood_tag", string(st.CurrentMoodTag)),
			zap.Float64("total_affection", st.AffectionLevel),
		)
	})

	if err != nil {
		h.logger.Error("Failed to subscribe to agent.emotion.delta", zap.Error(err))
		return err
	}

	h.logger.Info("EmotionDeltaHandler subscribed to agent.emotion.delta")
	return nil
}
