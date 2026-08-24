package bus

import (
	"bytes"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/nats-io/nats.go"
	"go.uber.org/zap"

	"betteragent-core/internal/schema"
)

const (
	SubjectTick                 = "agent.tick"
	SubjectInboundMessage       = "agent.inbound_message"
	SubjectEnrichContextReq     = "agent.enrich_context_req"
	SubjectReasoningRequest     = "agent.reasoning_request"
	SubjectReasoningCompleted   = "agent.reasoning_completed"
	SubjectActionCompleted      = "agent.action_completed"
	SubjectConsolidateMemoryReq = "agent.consolidate_memory_req"
	SubjectError                = "agent.error"

	// Digital Human & Realtime Multimodal Subjects
	SubjectSpeechStart   = "agent.speech.start"
	SubjectSpeechEnd     = "agent.speech.end"
	SubjectUserInterrupt = "agent.user.interrupt"
	SubjectAudioChunk    = "agent.audio.chunk"
	SubjectVisemeData    = "agent.viseme.data"
	SubjectEmotionUpdate = "agent.emotion.update"
	SubjectEmotionDelta  = "agent.emotion.delta"
	SubjectVisionFrame   = "agent.vision.frame"

	// 7 Realtime Cancelable Streaming Subjects
	SubjectSTTStreamChunk    = "agent.stt.stream_chunk"
	SubjectSTTStreamFinal    = "agent.stt.stream_final"
	SubjectTTSStreamChunk    = "agent.tts.stream_chunk"
	SubjectTTSStreamEnd      = "agent.tts.stream_end"
	SubjectStreamCancelReq   = "agent.stream.cancel_req"
	SubjectStreamCancelAck   = "agent.stream.cancel_ack"
	SubjectStreamStateChange = "agent.stream.state_change"

	// SubjectGameEvent carries external game events (e.g. Slay the Spike 2
	// C# mod hook) into the bus for observability/future consumers. The
	// UrgeEngine side effect happens in-process in the HTTP handler, not via
	// this subscription -- see webgateway/game_event_handler.go.
	SubjectGameEvent = "agent.game_event"
)

// ActionDecisionSubject and ActionDecisionWildcard replace the old flat
// SubjectActionDecision constant with a per-channel-per-chat NATS subject
// hierarchy (agent.action.{channel}.{chat_id}), so WebGateway/GotdAdapter/TTS
// can each subscribe only to their own channel and NATS routes at the broker
// level -- no more broadcast-then-filter-by-payload-field. channel is never
// hardcoded here; it's whatever string the caller already has in
// source_channel, so adding a future platform (Discord/Twitch) needs no
// changes to this function.
func ActionDecisionSubject(channel string, chatID int64) string {
	return fmt.Sprintf("agent.action.%s.%d", channel, chatID)
}

func ActionDecisionWildcard(channel string) string {
	return fmt.Sprintf("agent.action.%s.*", channel)
}

type NatsBus struct {
	nc     *nats.Conn
	js     nats.JetStreamContext
	logger *zap.Logger
}

func NewNatsBus(url string, user string, password string, logger *zap.Logger) (*NatsBus, error) {
	nc, err := nats.Connect(url,
		nats.UserInfo(user, password),
		nats.Name("betteragent-core"),
		nats.MaxReconnects(-1),
		nats.ReconnectWait(2*time.Second),
		nats.DisconnectErrHandler(func(c *nats.Conn, err error) {
			if err != nil {
				logger.Warn("NATS connection disconnected", zap.Error(err))
			}
		}),
		nats.ReconnectHandler(func(c *nats.Conn) {
			logger.Info("NATS connection reconnected successfully", zap.String("url", c.ConnectedUrl()))
		}),
	)
	if err != nil {
		logger.Warn("NATS Connection warning (running in offline bus mode)", zap.String("url", url), zap.Error(err))
		return &NatsBus{
			nc:     nil,
			js:     nil,
			logger: logger,
		}, nil
	}

	js, jsErr := nc.JetStream()
	if jsErr == nil {
		// Provision JetStream Stream for guaranteed event persistence (excluding RPC request-reply subjects)
		_, _ = js.AddStream(&nats.StreamConfig{
			Name:     "BETTERAGENT_EVENTS",
			Subjects: []string{"agent.events.>", "agent.action.>", "agent.reasoning_completed", "agent.inbound_message", "agent.tick"},
			Storage:  nats.MemoryStorage, // Or FileStorage
		})
		logger.Info("NATS JetStream Stream 'BETTERAGENT_EVENTS' initialized")
	}

	logger.Info("NATS Connected successfully", zap.String("url", url))
	return &NatsBus{
		nc:     nc,
		js:     js,
		logger: logger,
	}, nil
}

func (b *NatsBus) Publish(subject string, source string, payload interface{}) error {
	if b.nc == nil {
		b.logger.Debug("NATS offline: skip publish", zap.String("subject", subject))
		return nil
	}

	envelope := schema.EventEnvelope{
		ID:        uuid.New().String(),
		Subject:   subject,
		Timestamp: float64(time.Now().UnixNano()) / 1e9,
		Source:    source,
		Payload:   payload,
	}

	data, err := json.Marshal(envelope)
	if err != nil {
		return fmt.Errorf("failed to marshal event envelope: %w", err)
	}

	return b.nc.Publish(subject, data)
}

func (b *NatsBus) Subscribe(subject string, handler func(msg *nats.Msg)) (*nats.Subscription, error) {
	if b.nc == nil {
		b.logger.Debug("NATS offline: skip subscribe", zap.String("subject", subject))
		return nil, nil
	}
	return b.nc.Subscribe(subject, handler)
}

func (b *NatsBus) Request(subject string, source string, payload interface{}, timeout time.Duration) (*schema.ReasoningRequestPayload, error) {
	if b.nc == nil {
		return nil, fmt.Errorf("NATS offline")
	}

	envelope := schema.EventEnvelope{
		ID:        uuid.New().String(),
		Subject:   subject,
		Timestamp: float64(time.Now().UnixNano()) / 1e9,
		Source:    source,
		Payload:   payload,
	}

	data, err := json.Marshal(envelope)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	// Retry loop with exponential backoff (max 3 attempts)
	var lastErr error
	maxAttempts := 3
	perAttemptTimeout := timeout / time.Duration(maxAttempts)
	if perAttemptTimeout < 1500*time.Millisecond {
		perAttemptTimeout = 1500 * time.Millisecond
	}

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		msg, err := b.nc.Request(subject, data, perAttemptTimeout)
		if err != nil {
			lastErr = err
			b.logger.Warn("NATS Request attempt failed, retrying...",
				zap.String("subject", subject),
				zap.Int("attempt", attempt),
				zap.Error(err),
			)
			time.Sleep(time.Duration(attempt*300) * time.Millisecond)
			continue
		}

		// Filter out JetStream ACK packets if received on RPC reply inbox
		if bytes.Contains(msg.Data, []byte("\"stream\":")) {
			b.logger.Debug("Ignoring JetStream ACK on RPC reply inbox", zap.String("subject", subject), zap.String("ack", string(msg.Data)))
			continue
		}

		var respEnvelope struct {
			ID        string                         `json:"id"`
			Subject   string                         `json:"subject"`
			Timestamp float64                        `json:"timestamp"`
			Source    string                         `json:"source"`
			Payload   schema.ReasoningRequestPayload `json:"payload"`
		}
		if err := json.Unmarshal(msg.Data, &respEnvelope); err == nil && respEnvelope.Payload.ChatID != 0 {
			respEnvelope.Payload.EnsureDefaults()
			return &respEnvelope.Payload, nil
		}

		// Fallback: Direct unmarshal as ReasoningRequestPayload
		var reasoningReq schema.ReasoningRequestPayload
		if err := json.Unmarshal(msg.Data, &reasoningReq); err == nil && reasoningReq.ChatID != 0 {
			reasoningReq.EnsureDefaults()
			return &reasoningReq, nil
		}

		b.logger.Warn("NATS Request payload unmarshal failed on received message", zap.String("subject", subject), zap.String("data", string(msg.Data)))
		return nil, fmt.Errorf("failed to unmarshal ReasoningRequestPayload from NATS response")
	}

	return nil, fmt.Errorf("NATS Request failed after %d attempts on subject %s: %w", maxAttempts, subject, lastErr)
}

func (b *NatsBus) IsOffline() bool {
	if b == nil || b.nc == nil {
		return true
	}
	return b.nc.Status() == nats.CLOSED
}

func (b *NatsBus) Close() {
	if b.nc != nil {
		b.nc.Close()
	}
}
