package engine

import (
	"time"

	"go.uber.org/zap"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/idspace"
	"betteragent-core/internal/schema"
)

// PublishGameTurn starts a reasoning turn caused by an autonomous-play game
// (e.g. Slay the Spire 2, via webgateway/game_turn_handler.go's
// /api/game-turn endpoint) reaching a decision point. Mirrors
// PublishProactiveTurn's enrich->reasoning handoff (see proactive_trigger.go)
// almost line-for-line, but with TriggerType "game_turn" and no
// ProactiveReason -- kept as a separate file rather than folded into
// proactive_trigger.go since it's a distinct concept (externally triggered
// by a game reaching an actionable state, not by the Urge engine crossing a
// boredom threshold) with its own doc comments.
func PublishGameTurn(
	b *bus.NatsBus,
	csm *CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	targetChatID int64,
	logger *zap.Logger,
) {
	csm.TransitionToChat(targetChatID, StateThinking, "game_turn")

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

	req := schema.EnrichContextReqPayload{
		BasePayload:            schema.NewBasePayload("game_turn_trigger"),
		ChatID:                 targetChatID,
		UserID:                 targetChatID,
		GenerationID:           csm.GetGenerationChat(targetChatID),
		InboundMessage:         nil,
		CurrentState:           string(StateThinking),
		TriggerType:            "game_turn",
		EmotionDescription:     emotionDesc,
		PersonalityDescription: personalityDesc,
		CircadianDescription:   circadianDesc,
		SourceChannel:          sourceChannel,
	}

	reasoningReq, err := b.Request(bus.SubjectEnrichContextReq, "game_turn_trigger", req, 5*time.Second)
	if err != nil {
		logger.Warn("Game turn EnrichContext timeout/fallback to async publish", zap.Int64("chat_id", targetChatID), zap.Error(err))
		if pubErr := b.Publish(bus.SubjectEnrichContextReq, "game_turn_trigger", req); pubErr != nil {
			logger.Error("Failed to publish game turn EnrichContextReq fallback to NATS", zap.Int64("chat_id", targetChatID), zap.Error(pubErr))
		}
		return
	}
	if reasoningReq.ChatID == 0 {
		reasoningReq.ChatID = targetChatID
	}
	if reasoningReq.UserID == 0 {
		reasoningReq.UserID = targetChatID
	}

	logger.Info("🎮 Game turn fired -> NATS agent.reasoning_request",
		zap.Int64("chat_id", targetChatID),
		zap.String("source_channel", sourceChannel),
	)
	if err := b.Publish(bus.SubjectReasoningRequest, "game_turn_trigger", reasoningReq); err != nil {
		logger.Error("Failed to publish game turn ReasoningRequest to NATS", zap.Int64("chat_id", targetChatID), zap.Error(err))
	}
}
