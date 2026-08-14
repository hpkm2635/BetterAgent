package engine

import (
	"time"

	"go.uber.org/zap"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/idspace"
	"betteragent-core/internal/schema"
)

// PublishProactiveTurn starts a reasoning turn that was not caused by any
// inbound user message -- UrgeEngine crossing its threshold. Mirrors
// webgateway.NatsBridge.publishInboundMessage's enrich->reasoning handoff,
// minus the InboundMessage (there isn't one) and with TriggerType
// "proactive" instead of "user_message". Deliberately lives in engine
// rather than webgateway/gotd: it only needs bus/schema/emotion/
// CentralStateMachine, all already reachable from engine without creating
// an import cycle (webgateway and gotd import engine, never the reverse).
func PublishProactiveTurn(
	b *bus.NatsBus,
	csm *CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	targetChatID int64,
	reason string,
	logger *zap.Logger,
) {
	csm.TransitionToChat(targetChatID, StateThinking, "proactive_urge")

	sourceChannel := "telegram"
	if idspace.IsWebChat(targetChatID) {
		sourceChannel = "web"
	}

	var emotionDesc, personalityDesc, circadianDesc string
	if emoState != nil {
		emotionDesc = emoState.ToPromptDescription()
	}
	if personality != nil {
		personalityDesc = personality.ToPromptDescription()
	}
	if circadian != nil {
		circadianDesc = circadian.ToPromptDescription()
	}

	reasonCopy := reason
	req := schema.EnrichContextReqPayload{
		BasePayload:            schema.NewBasePayload("clock_engine"),
		ChatID:                 targetChatID,
		UserID:                 targetChatID,
		GenerationID:           csm.GetGenerationChat(targetChatID),
		InboundMessage:         nil,
		CurrentState:           string(StateThinking),
		TriggerType:            "proactive",
		EmotionDescription:     emotionDesc,
		PersonalityDescription: personalityDesc,
		CircadianDescription:   circadianDesc,
		SourceChannel:          sourceChannel,
		ProactiveReason:        &reasonCopy,
		IsProactiveOpportunity: true,
	}

	reasoningReq, err := b.Request(bus.SubjectEnrichContextReq, "clock_engine", req, 5*time.Second)
	if err != nil {
		logger.Warn("Proactive EnrichContext timeout/fallback to async publish", zap.Int64("chat_id", targetChatID), zap.Error(err))
		if pubErr := b.Publish(bus.SubjectEnrichContextReq, "clock_engine", req); pubErr != nil {
			logger.Error("Failed to publish proactive EnrichContextReq fallback to NATS", zap.Int64("chat_id", targetChatID), zap.Error(pubErr))
		}
		return
	}
	if reasoningReq.ChatID == 0 {
		reasoningReq.ChatID = targetChatID
	}
	if reasoningReq.UserID == 0 {
		reasoningReq.UserID = targetChatID
	}

	logger.Info("🎲 Proactive Urge fired -> NATS agent.reasoning_request",
		zap.Int64("chat_id", targetChatID),
		zap.String("reason", reason),
		zap.String("source_channel", sourceChannel),
	)
	if err := b.Publish(bus.SubjectReasoningRequest, "clock_engine", reasoningReq); err != nil {
		logger.Error("Failed to publish proactive ReasoningRequest to NATS", zap.Int64("chat_id", targetChatID), zap.Error(err))
	}
}
