package schema

import "time"

// EventEnvelope is the outer NATS wrapper.
type EventEnvelope struct {
	ID        string      `json:"id"`
	Subject   string      `json:"subject"`
	Timestamp float64     `json:"timestamp"`
	Source    string      `json:"source"`
	Payload   interface{} `json:"payload"`
}

type BasePayload struct {
	EventID         string  `json:"event_id"`
	Timestamp       float64 `json:"timestamp"`
	SourceComponent string  `json:"source_component"`
}

type InboundMessagePayload struct {
	BasePayload
	ChatID            int64   `json:"chat_id"`
	UserID            int64   `json:"user_id"`
	GenerationID      uint64  `json:"generation_id,omitempty"`
	MessageID         int     `json:"message_id"`
	SourceChannel     string  `json:"source_channel,omitempty"` // "telegram" | "web"
	RawText           *string `json:"raw_text,omitempty"`
	FilePath          *string `json:"file_path,omitempty"`
	ReplyToMessageID  *int    `json:"reply_to_message_id,omitempty"`
	MediaType         *string `json:"media_type,omitempty"`
	VoiceTranscript   *string `json:"voice_transcript,omitempty"`
	ChatType          string  `json:"chat_type"`
	SenderUsername    *string `json:"sender_username,omitempty"`
	ImageDescription  *string `json:"image_description,omitempty"`
	SenderFirstName   string  `json:"sender_first_name"`
	SenderLastName    *string `json:"sender_last_name,omitempty"`
	SenderDisplayName string  `json:"sender_display_name"`
}

type TickPayload struct {
	BasePayload
	ISOTime             string  `json:"iso_time"`
	TimeOfDay           string  `json:"time_of_day"`
	IdleDurationSeconds float64 `json:"idle_duration_seconds"`
	IsSleepHours        bool    `json:"is_sleep_hours"`
	TickCounter         int     `json:"tick_counter"`
	EmotionDescription  string  `json:"emotion_description"`
}

type EnrichContextReqPayload struct {
	BasePayload
	ChatID                 int64                  `json:"chat_id"`
	UserID                 int64                  `json:"user_id"`
	GenerationID           uint64                 `json:"generation_id,omitempty"`
	InboundMessage         *InboundMessagePayload `json:"inbound_message,omitempty"`
	CurrentState           string                 `json:"current_state"`
	TriggerType            string                 `json:"trigger_type"` // "user_message" | "proactive" | "tick" | "game_turn"
	EmotionDescription     string                 `json:"emotion_description"`
	PersonalityDescription string                 `json:"personality_description"`
	CircadianDescription   string                 `json:"circadian_description"`
	// SourceChannel, ProactiveReason and IsProactiveOpportunity carry a
	// proactive turn's routing/context through memory_hub.py into
	// ReasoningRequestPayload -- without SourceChannel a proactive turn (no
	// InboundMessage to derive it from) would fall back to "telegram" and
	// get silently dropped if the target was actually a web session.
	SourceChannel          string  `json:"source_channel,omitempty"` // "telegram" | "web"
	ProactiveReason        *string `json:"proactive_reason,omitempty"`
	IsProactiveOpportunity bool    `json:"is_proactive_opportunity,omitempty"`
}

type ReasoningRequestPayload struct {
	BasePayload
	ChatID                 int64                    `json:"chat_id"`
	UserID                 int64                    `json:"user_id"`
	GenerationID           uint64                   `json:"generation_id,omitempty"`
	SystemPromptOverride   *string                  `json:"system_prompt_override,omitempty"`
	ShortTermHistory       []map[string]interface{} `json:"short_term_history"`
	UserProfile            map[string]interface{}   `json:"user_profile"`
	RAGFacts               []string                 `json:"rag_facts"`
	ProactiveReason        *string                  `json:"proactive_reason,omitempty"`
	CurrentEmotion         string                   `json:"current_emotion"`
	PersonalityDescription string                   `json:"personality_description,omitempty"`
	CircadianDescription   string                   `json:"circadian_description,omitempty"`
	MoodScore              float64                  `json:"mood_score"`
	FormattedTimeStr       string                   `json:"formatted_time_str"`
	InboundMessage         *InboundMessagePayload   `json:"inbound_message,omitempty"`
	TriggerType            *string                  `json:"trigger_type,omitempty"` // "user_message" | "proactive" | "tick" | "game_turn"
	// SourceChannel/IsProactiveOpportunity must stay on this struct (not
	// just EnrichContextReqPayload) -- NatsBus.Request unmarshals the
	// memory_hub.py reply into this exact Go type and re-marshals it when
	// forwarding to SubjectReasoningRequest, so any field missing here gets
	// silently dropped on that round trip even though Python set it.
	SourceChannel          string `json:"source_channel,omitempty"`
	IsProactiveOpportunity bool   `json:"is_proactive_opportunity,omitempty"`
}

type EmotionDeltaPayload struct {
	BasePayload
	ChatID         int64   `json:"chat_id"`
	DeltaValence   float64 `json:"delta_valence"`
	DeltaArousal   float64 `json:"delta_arousal"`
	DeltaAffection float64 `json:"delta_affection"`
	IsJealous      bool    `json:"is_jealous"`
}

func (r *ReasoningRequestPayload) EnsureDefaults() {
	if r.ShortTermHistory == nil {
		r.ShortTermHistory = []map[string]interface{}{}
	}
	if r.UserProfile == nil {
		r.UserProfile = map[string]interface{}{}
	}
	if r.RAGFacts == nil {
		r.RAGFacts = []string{}
	}
}

type ActionDecisionPayload struct {
	BasePayload
	ChatID           int64   `json:"chat_id"`
	GenerationID     uint64  `json:"generation_id,omitempty"`
	SourceChannel    string  `json:"source_channel,omitempty"` // "telegram" | "web"
	ActionType       string  `json:"action_type"`
	TextContent      *string `json:"text_content,omitempty"`
	TypingDelay      float64 `json:"typing_delay"`
	MediaType        *string `json:"media_type,omitempty"`
	ReplyToMessageID *int    `json:"reply_to_message_id,omitempty"`
	VoicePath        *string `json:"voice_path,omitempty"`
	PhotoPath        *string `json:"photo_path,omitempty"`
	ChatAction       *string `json:"chat_action,omitempty"`
	StickerID        *string `json:"sticker_id,omitempty"`
	ReactionEmoji    *string `json:"reaction_emoji,omitempty"`
	IsFinal          bool    `json:"is_final,omitempty"`
}

type ActionCompletedPayload struct {
	BasePayload
	ChatID         int64                 `json:"chat_id"`
	SentMessageID  *int                  `json:"sent_message_id,omitempty"`
	ActionDecision ActionDecisionPayload `json:"action_decision"`
	Status         string                `json:"status"`
	SentTime       float64               `json:"sent_time"`
	ErrorDetail    *string               `json:"error_detail,omitempty"`
}

type ErrorPayload struct {
	BasePayload
	ErrorCode       string  `json:"error_code"`
	ErrorMessage    string  `json:"error_message"`
	StackTrace      *string `json:"stack_trace,omitempty"`
	CausedByEventID *string `json:"caused_by_event_id,omitempty"`
}

type StreamAudioChunkPayload struct {
	BasePayload
	ChatID       int64    `json:"chat_id"`
	GenerationID uint64   `json:"generation_id,omitempty"`
	AudioBase64  string   `json:"audio_base64"`
	SampleRate   int      `json:"sample_rate"`
	Format       string   `json:"format"`
	Visemes      []Viseme `json:"visemes,omitempty"`
}

type Viseme struct {
	TimeOffset float64 `json:"time_offset"`
	VisemeID   int     `json:"viseme_id"`
	Shape      string  `json:"shape"`
}

type EmotionUpdatePayload struct {
	BasePayload
	ChatID  int64  `json:"chat_id"`
	Emotion string `json:"emotion"`
	Action  string `json:"action,omitempty"`
}

type UserInterruptPayload struct {
	BasePayload
	ChatID int64 `json:"chat_id"`
	UserID int64 `json:"user_id"`
}

// SpeechBoundaryPayload marks a speech_start/speech_end VAD boundary for a
// chat -- carries nothing beyond identity because services/stt keys its
// per-chat FunASR sessions purely off ChatID, matching how every other
// per-chat NATS payload in this system is keyed.
type SpeechBoundaryPayload struct {
	BasePayload
	ChatID       int64  `json:"chat_id"`
	GenerationID uint64 `json:"generation_id,omitempty"`
}

// STTFinalTranscriptPayload is published by services/stt once FunASR returns
// a punctuation-restored final result for an utterance.
type STTFinalTranscriptPayload struct {
	BasePayload
	ChatID        int64  `json:"chat_id"`
	GenerationID  uint64 `json:"generation_id,omitempty"`
	Text          string `json:"text"`
	SourceChannel string `json:"source_channel,omitempty"`
}

type VisionFramePayload struct {
	BasePayload
	ChatID      int64  `json:"chat_id"`
	ImageBase64 string `json:"image_base64"`
	Format      string `json:"format"`      // "jpeg" | "webp"
	SourceType  string `json:"source_type"` // "screen" | "camera"
}

type StreamChunkPayload struct {
	BasePayload
	ChatID        int64    `json:"chat_id"`
	GenerationID  uint64   `json:"generation_id"`
	ChunkIndex    int      `json:"chunk_index"`
	IsFinal       bool     `json:"is_final"`
	SourceChannel string   `json:"source_channel,omitempty"`
	TextDelta     string   `json:"text_delta,omitempty"`
	AudioBase64   string   `json:"audio_base64,omitempty"`
	SampleRate    int      `json:"sample_rate,omitempty"`
	Format        string   `json:"format,omitempty"`
	Visemes       []Viseme `json:"visemes,omitempty"`
}

type StreamCancelPayload struct {
	BasePayload
	ChatID        int64  `json:"chat_id"`
	GenerationID  uint64 `json:"generation_id"`
	Reason        string `json:"reason"`
	SourceChannel string `json:"source_channel,omitempty"`
}

type PersonaUpdatePayload struct {
	BasePayload
	PersonaID       string `json:"persona_id"`
	Name            string `json:"name,omitempty"`
	Appearance      string `json:"appearance,omitempty"`
	BasePrompt      string `json:"base_prompt,omitempty"`
	SleepyPrompt    string `json:"sleepy_prompt,omitempty"`
	KnowledgeScope  string `json:"knowledge_scope,omitempty"`
	ForbiddenTopics string `json:"forbidden_topics,omitempty"`
}

type StreamStateChangePayload struct {
	BasePayload
	ChatID        int64  `json:"chat_id"`
	GenerationID  uint64 `json:"generation_id"`
	State         string `json:"state"`
	SourceChannel string `json:"source_channel,omitempty"`
}

// GameEventPayload carries an external game event (e.g. from the Slay the
// Spike 2 C# mod hook, POSTed to WebGateway's /api/game-event) onward for
// observability/future consumers. Weight is always server-resolved from the
// game_events config table, never trusted from the client -- see
// webgateway/game_event_handler.go.
type GameEventPayload struct {
	BasePayload
	Game      string                 `json:"game"`
	EventType string                 `json:"event_type"`
	Weight    float64                `json:"weight"`
	Detail    *string                `json:"detail,omitempty"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
}

func NewBasePayload(source string) BasePayload {
	return BasePayload{
		Timestamp:       float64(time.Now().UnixNano()) / 1e9,
		SourceComponent: source,
	}
}
