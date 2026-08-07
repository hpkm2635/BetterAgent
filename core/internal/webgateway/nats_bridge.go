package webgateway

import (
	"encoding/base64"
	"encoding/json"
	"time"

	"github.com/nats-io/nats.go"
	"go.uber.org/zap"
	"nhooyr.io/websocket"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/engine"
	"betteragent-core/internal/schema"
)

type NatsBridge struct {
	bus            *bus.NatsBus
	sessions       *SessionManager
	csm            *engine.CentralStateMachine
	emotionalState *emotion.EmotionalState
	personality    *emotion.PersonalityProfile
	circadian      *emotion.CircadianRhythmEvaluator
	logger         *zap.Logger
}

func newNatsBridge(
	bus *bus.NatsBus,
	sessions *SessionManager,
	csm *engine.CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	logger *zap.Logger,
) *NatsBridge {
	return &NatsBridge{
		bus:            bus,
		sessions:       sessions,
		csm:            csm,
		emotionalState: emoState,
		personality:    personality,
		circadian:      circadian,
		logger:         logger,
	}
}

func (b *NatsBridge) StartSubscriptions() error {
	// 1. Subscribe to Action Decisions from LLM / Cognitive Engine
	_, err := b.bus.Subscribe(bus.SubjectActionDecision, func(msg *nats.Msg) {
		b.handleActionDecisionMsg(msg)
	})
	if err != nil {
		return err
	}

	// 2. Subscribe to Realtime Audio Chunks (TTS / Viseme)
	_, _ = b.bus.Subscribe(bus.SubjectAudioChunk, func(msg *nats.Msg) {
		b.handleAudioChunkMsg(msg)
	})

	// 3. Subscribe to Realtime Emotion Updates
	_, _ = b.bus.Subscribe(bus.SubjectEmotionUpdate, func(msg *nats.Msg) {
		b.handleEmotionUpdateMsg(msg)
	})

	b.logger.Info("NATS Bridge subscriptions initialized for WebGateway")
	return nil
}

func (b *NatsBridge) HandleUserWSMessage(session *ClientSession, msgType websocket.MessageType, rawMsg []byte) {
	// Binary Audio Frame handling
	if msgType == websocket.MessageBinary {
		chatID, genID, rawAudio, err := DecodeBinaryAudioFrame(rawMsg)
		if err != nil {
			b.logger.Warn("Failed to decode binary audio frame", zap.Error(err))
			return
		}
		if chatID == 0 {
			chatID = session.ChatID
		}
		b.logger.Debug("Received Binary Audio Frame", zap.Int64("chat_id", chatID), zap.Uint64("gen_id", genID), zap.Int("bytes", len(rawAudio)))
		return
	}

	// JSON WSMessage handling
	var wsMsg WSMessage
	if err := json.Unmarshal(rawMsg, &wsMsg); err != nil {
		b.logger.Warn("Failed to unmarshal WSMessage", zap.Error(err), zap.String("raw", string(rawMsg)))
		return
	}

	switch wsMsg.Type {
	case "user.text":
		var p UserTextMessagePayload
		if err := json.Unmarshal(wsMsg.Payload, &p); err == nil && p.Text != "" {
			chatID := p.ChatID
			if chatID == 0 {
				chatID = session.ChatID
			}

			var currentGen uint64 = 1
			if b.csm != nil {
				currentGen = b.csm.GetGenerationChat(chatID)
			}

			inbound := schema.InboundMessagePayload{
				BasePayload:       schema.NewBasePayload("web_gateway"),
				ChatID:            chatID,
				UserID:            chatID,
				GenerationID:      currentGen,
				SourceChannel:     "web",
				RawText:           &p.Text,
				ChatType:          "private",
				SenderUsername:    strPtr("web_user"),
				SenderDisplayName: "Web Master",
			}

			b.logger.Info("WebGateway User Text -> NATS agent.inbound_message", zap.Int64("chat_id", chatID), zap.Uint64("gen_id", currentGen), zap.String("text", p.Text))
			_ = b.bus.Publish(bus.SubjectInboundMessage, "web_gateway", inbound)

			// 1. Central State Machine transition to THINKING
			if b.csm != nil {
				b.csm.TransitionToChat(chatID, engine.StateThinking, "inbound_message")
			}

			// 2. Build EnrichContextReqPayload
			enrichReq := schema.EnrichContextReqPayload{
				BasePayload:            schema.NewBasePayload("web_gateway"),
				ChatID:                 chatID,
				UserID:                 chatID,
				GenerationID:           currentGen,
				InboundMessage:         &inbound,
				CurrentState:           "THINKING",
				TriggerType:            "user_message",
				EmotionDescription:     b.getEmotionDesc(),
				PersonalityDescription: b.getPersonalityDesc(),
				CircadianDescription:   b.getCircadianDesc(),
			}

			// 3. Request EnrichContext synchronously via NATS (5s timeout) ➔ Publish ReasoningRequest to Python cognitive_service
			go func(cID int64, req schema.EnrichContextReqPayload) {
				reasoningReq, err := b.bus.Request(bus.SubjectEnrichContextReq, "web_gateway", req, 5*time.Second)
				if err != nil {
					b.logger.Warn("WebGateway EnrichContext timeout/fallback to async publish", zap.Error(err))
					_ = b.bus.Publish(bus.SubjectEnrichContextReq, "web_gateway", req)
					return
				}
				if reasoningReq.ChatID == 0 {
					reasoningReq.ChatID = cID
				}
				if reasoningReq.UserID == 0 {
					reasoningReq.UserID = cID
				}
				b.logger.Info("WebGateway EnrichContext -> NATS agent.reasoning_request to Python Cognitive Engine", zap.Int64("chat_id", cID))
				_ = b.bus.Publish(bus.SubjectReasoningRequest, "web_gateway", reasoningReq)
			}(chatID, enrichReq)
		}

	case "user.speech_start", "user.interrupt":
		chatID := session.ChatID
		var newGenID uint64 = 1
		if b.csm != nil {
			newGenID = b.csm.IncrementGenerationChat(chatID)
			b.csm.TransitionToChat(chatID, engine.StateCancelling, "user_barge_in_interrupt")
		}

		// 1. Immediately purge any queued outbound audio/text/emotion chunks in WebGateway send buffer
		b.sessions.ClearChatBuffers(chatID)

		cancelPayload := schema.StreamCancelPayload{
			BasePayload:   schema.NewBasePayload("web_gateway"),
			ChatID:        chatID,
			GenerationID:  newGenID,
			Reason:        "user_barge_in_interrupt",
			SourceChannel: "web",
		}
		b.logger.Info("⚡ User Speech Interrupt -> NATS agent.stream.cancel_req & agent.user.interrupt", zap.Int64("chat_id", chatID), zap.Uint64("gen_id", newGenID))
		_ = b.bus.Publish(bus.SubjectStreamCancelReq, "web_gateway", cancelPayload)
		_ = b.bus.Publish(bus.SubjectUserInterrupt, "web_gateway", cancelPayload)

		// 2s Auto-Recovery Timer for CANCELLING state if no user speech follows
		go func(cID int64, targetGen uint64) {
			time.Sleep(2 * time.Second)
			if b.csm != nil && b.csm.GetChatState(cID) == engine.StateCancelling && b.csm.GetGenerationChat(cID) == targetGen {
				b.csm.TransitionToChat(cID, engine.StateIdle, "cancel_auto_recovery_idle")
				ackPayload := schema.StreamCancelPayload{
					BasePayload:   schema.NewBasePayload("web_gateway"),
					ChatID:        cID,
					GenerationID:  targetGen,
					Reason:        "cancel_ack_auto_idle",
					SourceChannel: "web",
				}
				_ = b.bus.Publish(bus.SubjectStreamCancelAck, "web_gateway", ackPayload)
			}
		}(chatID, newGenID)

	case "user.speech_end":
		b.logger.Debug("WebGateway User Speech End", zap.Int64("chat_id", session.ChatID))

	case "user.vision_frame":
		var p UserVisionFramePayload
		if err := json.Unmarshal(wsMsg.Payload, &p); err == nil && p.ImageBase64 != "" {
			chatID := p.ChatID
			if chatID == 0 {
				chatID = session.ChatID
			}

			format := p.Format
			if format == "" {
				format = "jpeg"
			}
			sourceType := p.SourceType
			if sourceType == "" {
				sourceType = "screen"
			}

			visionFrame := schema.VisionFramePayload{
				BasePayload: schema.NewBasePayload("web_gateway"),
				ChatID:      chatID,
				ImageBase64: p.ImageBase64,
				Format:      format,
				SourceType:  sourceType,
			}

			b.logger.Info("📷 WebGateway Vision Frame -> NATS agent.vision_frame",
				zap.Int64("chat_id", chatID),
				zap.String("source_type", sourceType),
				zap.Int("image_base64_len", len(p.ImageBase64)),
			)
			_ = b.bus.Publish(bus.SubjectVisionFrame, "web_gateway", visionFrame)
		}

	default:
		b.logger.Warn("Unknown WSMessage Type", zap.String("type", wsMsg.Type))
	}
}

func (b *NatsBridge) handleActionDecisionMsg(msg *nats.Msg) {
	var env struct {
		Payload schema.ActionDecisionPayload `json:"payload"`
	}
	if err := json.Unmarshal(msg.Data, &env); err != nil {
		var direct schema.ActionDecisionPayload
		if err2 := json.Unmarshal(msg.Data, &direct); err2 == nil {
			env.Payload = direct
		} else {
			return
		}
	}

	decision := env.Payload

	// Explicit Channel Routing: Ignore action decisions intended for telegram or other non-web channels
	if decision.SourceChannel != "" && decision.SourceChannel != "web" {
		return
	}

	// Static Generation ID Filter: Drop stale action decisions from past turns
	if decision.GenerationID != 0 && b.csm != nil {
		activeGen := b.csm.GetGenerationChat(decision.ChatID)
		if decision.GenerationID != activeGen {
			b.logger.Warn("⚠️ Dropped stale ActionDecision from old generation", zap.Int64("chat_id", decision.ChatID), zap.Uint64("decision_gen", decision.GenerationID), zap.Uint64("active_gen", activeGen))
			return
		}
	}

	if decision.TextContent != nil && *decision.TextContent != "" {
		outBytes, _ := json.Marshal(WSMessage{
			Type: "agent.text_delta",
			Payload: marshalRaw(AgentTextDeltaPayload{
				Text: *decision.TextContent,
			}),
		})
		b.sessions.SendTextToChat(decision.ChatID, outBytes)
	}

	if decision.StickerID != nil || decision.ReactionEmoji != nil {
		emotionStr := "happy"
		if decision.ReactionEmoji != nil {
			emotionStr = *decision.ReactionEmoji
		}
		outBytes, _ := json.Marshal(WSMessage{
			Type: "agent.emotion",
			Payload: marshalRaw(AgentEmotionPayload{
				Emotion: emotionStr,
			}),
		})
		b.sessions.SendTextToChat(decision.ChatID, outBytes)
	}

	// 1. Central State Machine Management for Stream Reasoning & Audio:
	if b.csm != nil {
		if !decision.IsFinal {
			// Streaming in progress: Maintain STREAMING_TTS and extend watchdog
			b.csm.TransitionToChat(decision.ChatID, engine.StateStreamingTTS, "stream_reasoning_chunk")
			b.csm.TouchWatchdogChat(decision.ChatID, 30*time.Second)
		} else {
			// IsFinal == true: Text reasoning completed.
			// Set 5-second smooth audio flush window instead of jumping immediately to IDLE
			b.csm.TouchWatchdogChat(decision.ChatID, 5*time.Second)
		}
	}

	// 2. Publish ActionCompleted to NATS
	completedPayload := schema.ActionCompletedPayload{
		BasePayload:    schema.NewBasePayload("web_gateway"),
		ChatID:         decision.ChatID,
		ActionDecision: decision,
		Status:         "success",
		SentTime:       float64(time.Now().UnixNano()) / 1e9,
	}
	if err := b.bus.Publish(bus.SubjectActionCompleted, "web_gateway", completedPayload); err != nil {
		b.logger.Error("Failed to publish ActionCompleted to NATS in WebGateway", zap.Int64("chat_id", decision.ChatID), zap.Error(err))
	}
}

func (b *NatsBridge) handleAudioChunkMsg(msg *nats.Msg) {
	var env struct {
		Payload schema.StreamAudioChunkPayload `json:"payload"`
	}
	if err := json.Unmarshal(msg.Data, &env); err != nil {
		return
	}

	p := env.Payload

	// 1. Standard JSON WebSocket Message (for stage-web compat)
	visemes := make([]Viseme, len(p.Visemes))
	for i, v := range p.Visemes {
		visemes[i] = Viseme{
			TimeOffset: v.TimeOffset,
			VisemeID:   v.VisemeID,
			Shape:      v.Shape,
		}
	}

	outBytes, _ := json.Marshal(WSMessage{
		Type: "agent.audio_chunk",
		Payload: marshalRaw(AgentAudioChunkPayload{
			AudioBase64: p.AudioBase64,
			SampleRate:  p.SampleRate,
			Format:      p.Format,
			Visemes:     visemes,
		}),
	})
	b.sessions.SendTextToChat(p.ChatID, outBytes)

	// TouchWatchdog & Maintain STREAMING_TTS while audio chunks arrive
	if b.csm != nil {
		b.csm.TransitionToChat(p.ChatID, engine.StateStreamingTTS, "audio_chunk_streaming")
		b.csm.TouchWatchdogChat(p.ChatID, 30*time.Second)
	}

	// 2. High-Performance Zero-Copy Binary WebSocket Frame (0% Base64 Overhead)
	if rawAudioBytes, err := base64.StdEncoding.DecodeString(p.AudioBase64); err == nil && len(rawAudioBytes) > 0 {
		binFrame := EncodeBinaryAudioFrame(p.ChatID, b.csm.GetGenerationChat(p.ChatID), rawAudioBytes)
		b.sessions.SendBinaryToChat(p.ChatID, binFrame)
	}
}

func (b *NatsBridge) handleEmotionUpdateMsg(msg *nats.Msg) {
	var env struct {
		Payload schema.EmotionUpdatePayload `json:"payload"`
	}
	if err := json.Unmarshal(msg.Data, &env); err != nil {
		return
	}

	p := env.Payload
	outBytes, _ := json.Marshal(WSMessage{
		Type: "agent.emotion",
		Payload: marshalRaw(AgentEmotionPayload{
			Emotion: p.Emotion,
			Action:  p.Action,
		}),
	})
	b.sessions.SendTextToChat(p.ChatID, outBytes)
}

func (b *NatsBridge) getEmotionDesc() string {
	if b.emotionalState != nil {
		return b.emotionalState.ToPromptDescription()
	}
	return "Valence: 0.5 (Neutral)"
}

func (b *NatsBridge) getPersonalityDesc() string {
	if b.personality != nil {
		return b.personality.ToPromptDescription()
	}
	return "Tsundere Catgirl"
}

func (b *NatsBridge) getCircadianDesc() string {
	if b.circadian != nil {
		return b.circadian.ToPromptDescription()
	}
	return "Daytime"
}

func strPtr(s string) *string {
	return &s
}

func marshalRaw(v interface{}) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}
