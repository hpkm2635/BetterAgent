package webgateway

import (
	"encoding/base64"
	"encoding/json"
	"strings"
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
	bus                 *bus.NatsBus
	sessions            *SessionManager
	csm                 *engine.CentralStateMachine
	emotionalState      *emotion.EmotionalState
	personality         *emotion.PersonalityProfile
	circadian           *emotion.CircadianRhythmEvaluator
	urgeEngine          *engine.UrgeEngine
	autonomousPlayState *engine.AutonomousPlayState
	logger              *zap.Logger
}

func newNatsBridge(
	bus *bus.NatsBus,
	sessions *SessionManager,
	csm *engine.CentralStateMachine,
	emoState *emotion.EmotionalState,
	personality *emotion.PersonalityProfile,
	circadian *emotion.CircadianRhythmEvaluator,
	urgeEngine *engine.UrgeEngine,
	autonomousPlayState *engine.AutonomousPlayState,
	logger *zap.Logger,
) *NatsBridge {
	return &NatsBridge{
		bus:                 bus,
		sessions:            sessions,
		csm:                 csm,
		emotionalState:      emoState,
		personality:         personality,
		circadian:           circadian,
		urgeEngine:          urgeEngine,
		autonomousPlayState: autonomousPlayState,
		logger:              logger,
	}
}

func (b *NatsBridge) StartSubscriptions() error {
	// 1. Subscribe to Action Decisions from LLM / Cognitive Engine -- only
	// the "web" channel's subjects, so Telegram-bound decisions are never
	// even delivered here (see bus.ActionDecisionWildcard).
	_, err := b.bus.Subscribe(bus.ActionDecisionWildcard("web"), func(msg *nats.Msg) {
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

	// 4. Subscribe to STT Final Transcripts from services/stt -- treated
	// exactly like the user having typed the text (see publishInboundMessage).
	_, _ = b.bus.Subscribe(bus.SubjectSTTStreamFinal, func(msg *nats.Msg) {
		b.handleSTTFinalMsg(msg)
	})

	// 5. Subscribe to TTS Stream End & Stream Cancel Ack to broadcast CSM IDLE state to browser
	_, _ = b.bus.Subscribe(bus.SubjectTTSStreamEnd, func(msg *nats.Msg) {
		b.handleTTSStreamEndMsg(msg)
	})
	_, _ = b.bus.Subscribe(bus.SubjectStreamCancelAck, func(msg *nats.Msg) {
		b.handleStreamCancelAckMsg(msg)
	})

	b.logger.Info("NATS Bridge subscriptions initialized for WebGateway")
	return nil
}

func (b *NatsBridge) HandleUserWSMessage(session *ClientSession, msgType websocket.MessageType, rawMsg []byte) {
	// Binary Audio Frame handling (browser mic -> STT). Same wire format as
	// the backend->browser TTS direction (EncodeBinaryAudioFrame) -- reusing
	// it here avoids inventing a second header format for the other direction.
	if msgType == websocket.MessageBinary {
		chatID, genID, rawAudio, err := DecodeBinaryAudioFrame(rawMsg)
		if err != nil {
			b.logger.Warn("Failed to decode binary audio frame", zap.Error(err))
			return
		}
		if chatID == 0 {
			chatID = session.ChatID
		}
		b.publishSTTStreamChunk(chatID, genID, rawAudio)
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
			// chat_id is always pinned to the authenticated session, never
			// taken from the message body -- otherwise a client could
			// address (and read the memory of) an arbitrary chat_id per message.
			b.publishInboundMessage(session.ChatID, p.Text, "", nil)
		}

	case "user.interrupt":
		b.handleBargeInInterrupt(session.ChatID)

	case "user.speech_start":
		// Same "stop whatever the agent is currently saying" reaction as
		// user.interrupt (VAD firing speech_start while the agent is mid-
		// TALKING is exactly a barge-in), plus attempt to start a listening
		// session -- harmless no-op via IsValidTransition if there was
		// nothing to barge into (e.g. starting a fresh turn from IDLE).
		b.handleBargeInInterrupt(session.ChatID)
		if b.csm != nil {
			b.csm.TransitionToChat(session.ChatID, engine.StateListening, "vad_speech_start")
		}
		if err := b.bus.Publish(bus.SubjectSpeechStart, "web_gateway", b.speechBoundaryPayload(session.ChatID)); err != nil {
			b.logger.Error("Failed to publish SpeechStart to NATS", zap.Int64("chat_id", session.ChatID), zap.Error(err))
		}

	case "user.speech_end":
		if b.csm != nil {
			b.csm.TransitionToChat(session.ChatID, engine.StateStreamingSTT, "vad_speech_end")
		}
		if err := b.bus.Publish(bus.SubjectSpeechEnd, "web_gateway", b.speechBoundaryPayload(session.ChatID)); err != nil {
			b.logger.Error("Failed to publish SpeechEnd to NATS", zap.Int64("chat_id", session.ChatID), zap.Error(err))
		}

	case "user.vision_frame":
		var p UserVisionFramePayload
		if err := json.Unmarshal(wsMsg.Payload, &p); err == nil && p.ImageBase64 != "" {
			// See user.text above: chat_id always comes from the session, never the payload.
			chatID := session.ChatID

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
			if err := b.bus.Publish(bus.SubjectVisionFrame, "web_gateway", visionFrame); err != nil {
				b.logger.Error("Failed to publish VisionFrame to NATS", zap.Int64("chat_id", chatID), zap.Error(err))
			}
		}

	case "admin.persona_update":
		var p AdminPersonaUpdatePayload
		if err := json.Unmarshal(wsMsg.Payload, &p); err == nil && p.PersonaID != "" {
			personaPayload := schema.PersonaUpdatePayload{
				BasePayload:     schema.NewBasePayload("web_gateway"),
				PersonaID:       p.PersonaID,
				Name:            p.Name,
				Appearance:      p.Appearance,
				BasePrompt:      p.BasePrompt,
				SleepyPrompt:    p.SleepyPrompt,
				KnowledgeScope:  p.KnowledgeScope,
				ForbiddenTopics: p.ForbiddenTopics,
			}
			if err := b.bus.Publish("agent.persona.update", "web_gateway", personaPayload); err != nil {
				b.logger.Error("Failed to publish PersonaUpdate to NATS", zap.String("persona_id", p.PersonaID), zap.Error(err))
			} else {
				b.logger.Info("⚙️ Admin Persona Update -> NATS agent.persona.update",
					zap.String("persona_id", p.PersonaID),
				)
			}
		}

	default:
		b.logger.Warn("Unknown WSMessage Type", zap.String("type", wsMsg.Type))
	}
}

// handleBargeInInterrupt is the shared "stop whatever the agent is currently
// saying" reaction to both user.interrupt and user.speech_start (VAD firing
// speech_start while the agent is mid-TALKING is itself a barge-in). Safe to
// call with nothing to interrupt -- TransitionToChat(...StateCancelling...)
// is simply rejected by IsValidTransition from states like IDLE, and the
// resulting stray cancel signals are no-ops downstream (see
// services/cognitive and services/tts cancel handlers).
func (b *NatsBridge) handleBargeInInterrupt(chatID int64) {
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
	if err := b.bus.Publish(bus.SubjectStreamCancelReq, "web_gateway", cancelPayload); err != nil {
		b.logger.Error("Failed to publish StreamCancelReq to NATS", zap.Int64("chat_id", chatID), zap.Error(err))
	}
	if err := b.bus.Publish(bus.SubjectUserInterrupt, "web_gateway", cancelPayload); err != nil {
		b.logger.Error("Failed to publish UserInterrupt to NATS", zap.Int64("chat_id", chatID), zap.Error(err))
	}

	// 2s Auto-Recovery Timer for CANCELLING state if no user speech follows.
	// Self-guarding: only forces IDLE if the state/generation are still
	// exactly what this call set them to, so a StateListening transition
	// (see user.speech_start) that lands in the meantime is left alone.
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
			if err := b.bus.Publish(bus.SubjectStreamCancelAck, "web_gateway", ackPayload); err != nil {
				b.logger.Error("Failed to publish StreamCancelAck to NATS", zap.Int64("chat_id", cID), zap.Error(err))
			}
		}
	}(chatID, newGenID)
}

// publishSTTStreamChunk forwards one browser-mic audio chunk to services/stt.
// Binary end-to-end for the high-frequency browser<->WebGateway hop (see
// HandleUserWSMessage), base64+JSON for the NATS hop like every other
// payload in this system -- NATS core pub/sub overhead is negligible
// either way, so there's no reason to special-case this one internal hop.
func (b *NatsBridge) publishSTTStreamChunk(chatID int64, genID uint64, rawAudio []byte) {
	chunk := schema.StreamChunkPayload{
		BasePayload:   schema.NewBasePayload("web_gateway"),
		ChatID:        chatID,
		GenerationID:  genID,
		SourceChannel: "web",
		AudioBase64:   base64.StdEncoding.EncodeToString(rawAudio),
		SampleRate:    16000,
		Format:        "pcm",
	}
	if err := b.bus.Publish(bus.SubjectSTTStreamChunk, "web_gateway", chunk); err != nil {
		b.logger.Error("Failed to publish STT stream chunk to NATS", zap.Int64("chat_id", chatID), zap.Error(err))
	}
}

func (b *NatsBridge) speechBoundaryPayload(chatID int64) schema.SpeechBoundaryPayload {
	var genID uint64 = 1
	if b.csm != nil {
		genID = b.csm.GetGenerationChat(chatID)
	}
	return schema.SpeechBoundaryPayload{
		BasePayload:  schema.NewBasePayload("web_gateway"),
		ChatID:       chatID,
		GenerationID: genID,
	}
}

// publishInboundMessage is shared by user.text and the STT final-transcript
// subscriber (see StartSubscriptions) -- a finished voice transcript is
// treated exactly like the user having typed it, reusing the entire
// downstream reasoning/memory/TTS pipeline with zero special-casing there.
// mediaType/voiceTranscript are set only for the STT path (both zero-valued
// for plain typed text).
func (b *NatsBridge) publishInboundMessage(chatID int64, text string, mediaType string, voiceTranscript *string) {
	// /game_start and /game_stop are intercepted here, before anything else
	// touches NATS/CSM/the LLM -- deterministic, Go-side, and (for
	// /game_stop specifically) works as a genuine emergency stop precisely
	// because it never depends on the LLM cooperating. See
	// handleGameStartStopCommand's doc comment.
	if b.handleGameStartStopCommand(chatID, text) {
		return
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
		RawText:           &text,
		VoiceTranscript:   voiceTranscript,
		ChatType:          "private",
		SenderUsername:    strPtr("web_user"),
		SenderDisplayName: "Web Master",
	}
	if mediaType != "" {
		inbound.MediaType = &mediaType
	}

	b.logger.Info("WebGateway Inbound Message -> NATS agent.inbound_message", zap.Int64("chat_id", chatID), zap.Uint64("gen_id", currentGen), zap.String("text", text), zap.String("media_type", mediaType))
	if err := b.bus.Publish(bus.SubjectInboundMessage, "web_gateway", inbound); err != nil {
		b.logger.Error("Failed to publish InboundMessage to NATS", zap.Int64("chat_id", chatID), zap.Error(err))
	}

	// 1. Central State Machine transition to THINKING
	if b.csm != nil {
		b.csm.TransitionToChat(chatID, engine.StateThinking, "inbound_message")
		b.csm.TouchActivity(chatID)
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
		SourceChannel:          "web",
		EmotionDescription:     b.getEmotionDesc(),
		PersonalityDescription: b.getPersonalityDesc(),
		CircadianDescription:   b.getCircadianDesc(),
	}

	// 3. Request EnrichContext synchronously via NATS (5s timeout) ➔ Publish ReasoningRequest to Python cognitive_service
	go func(cID int64, req schema.EnrichContextReqPayload) {
		reasoningReq, err := b.bus.Request(bus.SubjectEnrichContextReq, "web_gateway", req, 5*time.Second)
		if err != nil {
			b.logger.Warn("WebGateway EnrichContext timeout/fallback to direct ReasoningRequest publish", zap.Error(err))
			fallbackReasoning := schema.ReasoningRequestPayload{
				BasePayload:    schema.NewBasePayload("web_gateway"),
				ChatID:         cID,
				UserID:         cID,
				GenerationID:   req.GenerationID,
				InboundMessage: req.InboundMessage,
				CurrentEmotion: req.EmotionDescription,
				TriggerType:    &req.TriggerType,
				SourceChannel:  "web",
			}
			if pubErr := b.bus.Publish(bus.SubjectReasoningRequest, "web_gateway", fallbackReasoning); pubErr != nil {
				b.logger.Error("Failed to publish fallback ReasoningRequest to NATS", zap.Int64("chat_id", cID), zap.Error(pubErr))
				if b.csm != nil {
					b.csm.TransitionToChat(cID, engine.StateIdle, "enrich_context_failed")
				}
			}
			return
		}
		if reasoningReq.ChatID == 0 {
			reasoningReq.ChatID = cID
		}
		if reasoningReq.UserID == 0 {
			reasoningReq.UserID = cID
		}
		b.logger.Info("WebGateway EnrichContext -> NATS agent.reasoning_request to Python Cognitive Engine", zap.Int64("chat_id", cID))
		if err := b.bus.Publish(bus.SubjectReasoningRequest, "web_gateway", reasoningReq); err != nil {
			b.logger.Error("Failed to publish ReasoningRequest to NATS", zap.Int64("chat_id", cID), zap.Error(err))
		}
	}(chatID, enrichReq)
}

// handleGameStartStopCommand intercepts "/game_start" and "/game_stop" as
// raw literal text, before anything else in publishInboundMessage runs --
// they never reach NATS/CSM/the LLM at all. Returns true if the text was
// one of these commands (caller should stop processing this message any
// further).
//
// /game_stop is the actual emergency stop for autonomous play: flipping
// AutonomousPlayState off only prevents *future* game turns from firing --
// it does nothing about a turn already in flight. Publishing
// SubjectStreamCancelReq/SubjectUserInterrupt (the same subjects barge-in
// already uses) additionally reaches cognitive_service's existing
// cancel_chat_stream and tears down any in-progress tool-calling round.
// This is deliberately deterministic and Go-side: it works even if the LLM
// is currently misbehaving, because it never depends on the LLM cooperating.
func (b *NatsBridge) handleGameStartStopCommand(chatID int64, text string) bool {
	if b.autonomousPlayState == nil {
		return false
	}

	cmd := strings.TrimSpace(text)
	if idx := strings.LastIndex(cmd, "/game_"); idx != -1 {
		cmd = cmd[idx:]
	}
	cmd = strings.TrimSpace(cmd)

	if strings.HasPrefix(cmd, "/game_start") {
		b.autonomousPlayState.Activate(chatID)
		b.logger.Info("🎮 Autonomous play activated", zap.Int64("chat_id", chatID))
		b.replyDirect(chatID, "游戏自动托管已开启喵～ 发送 /game_stop 可以随时叫停我。")
		return true
	}
	if strings.HasPrefix(cmd, "/game_stop") {
		deactivatedChatID := b.autonomousPlayState.Deactivate()
		b.logger.Info("🛑 Autonomous play deactivated", zap.Int64("chat_id", chatID))
		if deactivatedChatID != 0 {
			cancelPayload := schema.StreamCancelPayload{
				BasePayload:   schema.NewBasePayload("web_gateway"),
				ChatID:        deactivatedChatID,
				GenerationID:  b.csm.GetGenerationChat(deactivatedChatID),
				Reason:        "game_stop_emergency_cancel",
				SourceChannel: "web",
			}
			if err := b.bus.Publish(bus.SubjectStreamCancelReq, "web_gateway", cancelPayload); err != nil {
				b.logger.Error("Failed to publish StreamCancelReq on /game_stop", zap.Int64("chat_id", deactivatedChatID), zap.Error(err))
			}
			if err := b.bus.Publish(bus.SubjectUserInterrupt, "web_gateway", cancelPayload); err != nil {
				b.logger.Error("Failed to publish UserInterrupt on /game_stop", zap.Int64("chat_id", deactivatedChatID), zap.Error(err))
			}
		}
		b.replyDirect(chatID, "游戏自动托管已停止，操作权还给主人啦。")
		return true
	}
	return false
}

// replyDirect sends a WS text reply straight to the browser, bypassing
// NATS/LLM entirely -- used for the /game_start /game_stop confirmations,
// which must work even if the reasoning pipeline is stuck.
func (b *NatsBridge) replyDirect(chatID int64, text string) {
	outBytes, _ := json.Marshal(WSMessage{
		Type: "agent.text_delta",
		Payload: marshalRaw(AgentTextDeltaPayload{
			Text:    text,
			IsFinal: true,
		}),
	})
	b.sessions.SendTextToChat(chatID, outBytes)
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

	// Channel routing is now handled by NATS itself (subscribed only to
	// agent.action.web.* -- see StartSubscriptions). SourceChannel is a
	// self-reported payload field; the subject already proved this message
	// belongs to the web channel, so a mismatch here is a publisher bug
	// worth logging loudly, not a reason to drop a message NATS already
	// routed correctly.
	if decision.SourceChannel != "" && decision.SourceChannel != "web" {
		b.logger.Error("received ActionDecision on web-channel subject with mismatched source_channel payload field -- processing anyway, subject is authoritative post-refactor",
			zap.Int64("chat_id", decision.ChatID), zap.String("source_channel", decision.SourceChannel))
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
				Text:    *decision.TextContent,
				IsFinal: decision.IsFinal,
			}),
		})
		b.sessions.SendTextToChat(decision.ChatID, outBytes)

		stateBytes, _ := json.Marshal(WSMessage{
			Type: "agent.state_change",
			Payload: marshalRaw(map[string]interface{}{
				"state":     "talking",
				"csm_state": "TALKING",
				"chat_id":   decision.ChatID,
			}),
		})
		b.sessions.SendTextToChat(decision.ChatID, stateBytes)

		if decision.IsFinal {
			chatID := decision.ChatID
			genID := decision.GenerationID
			go func() {
				time.Sleep(3 * time.Second)
				if b.csm != nil {
					currState := b.csm.GetChatState(chatID)
					currGen := b.csm.GetGenerationChat(chatID)
					if (currState == engine.StateTalking || currState == engine.StateStreamingTTS) && (genID == 0 || currGen == genID) {
						b.csm.TransitionToChat(chatID, engine.StateIdle, "text_fallback_idle")
						out, _ := json.Marshal(WSMessage{
							Type: "agent.state_change",
							Payload: marshalRaw(map[string]interface{}{
								"state":     "idle",
								"csm_state": "IDLE",
								"chat_id":   chatID,
							}),
						})
						b.sessions.SendTextToChat(chatID, out)
					}
				}
			}()
		}
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
			if b.urgeEngine != nil {
				b.urgeEngine.OnTurnCompleted()
			}
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

func (b *NatsBridge) buildAgentEmotionPayload(emotionStr string, action string) AgentEmotionPayload {
	payload := AgentEmotionPayload{
		Emotion: emotionStr,
		Action:  action,
	}
	if b.emotionalState != nil {
		payload.Mood = string(b.emotionalState.CurrentMoodTag)
		payload.Valence = b.emotionalState.Valence
		payload.Arousal = b.emotionalState.Arousal
		payload.Energy = b.emotionalState.Energy
		payload.SocialBattery = b.emotionalState.SocialBattery
		payload.Affection = b.emotionalState.AffectionLevel
		payload.IsJealous = b.emotionalState.IsJealous
		payload.Description = b.emotionalState.ToPromptDescription()
	}
	return payload
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
		Type:    "agent.emotion",
		Payload: marshalRaw(b.buildAgentEmotionPayload(p.Emotion, p.Action)),
	})
	b.sessions.SendTextToChat(p.ChatID, outBytes)
}

func (b *NatsBridge) handleSTTFinalMsg(msg *nats.Msg) {
	var env struct {
		Payload schema.STTFinalTranscriptPayload `json:"payload"`
	}
	if err := json.Unmarshal(msg.Data, &env); err != nil {
		return
	}

	p := env.Payload
	if p.ChatID == 0 || p.Text == "" {
		return
	}

	transcript := p.Text
	b.publishInboundMessage(p.ChatID, p.Text, "voice", &transcript)
}

func (b *NatsBridge) handleTTSStreamEndMsg(msg *nats.Msg) {
	var env struct {
		Payload schema.StreamChunkPayload `json:"payload"`
	}
	if err := json.Unmarshal(msg.Data, &env); err != nil {
		return
	}

	p := env.Payload
	if p.ChatID == 0 {
		return
	}

	if b.csm != nil {
		b.csm.TransitionToChat(p.ChatID, engine.StateIdle, "tts_stream_end")
	}

	outBytes, _ := json.Marshal(WSMessage{
		Type: "agent.state_change",
		Payload: marshalRaw(map[string]interface{}{
			"state":     "idle",
			"csm_state": "IDLE",
			"chat_id":   p.ChatID,
		}),
	})
	b.sessions.SendTextToChat(p.ChatID, outBytes)
}

func (b *NatsBridge) handleStreamCancelAckMsg(msg *nats.Msg) {
	var env struct {
		Payload schema.StreamCancelPayload `json:"payload"`
	}
	if err := json.Unmarshal(msg.Data, &env); err != nil {
		return
	}

	p := env.Payload
	if p.ChatID == 0 {
		return
	}

	outBytes, _ := json.Marshal(WSMessage{
		Type: "agent.state_change",
		Payload: marshalRaw(map[string]interface{}{
			"state":     "idle",
			"csm_state": "IDLE",
			"chat_id":   p.ChatID,
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
