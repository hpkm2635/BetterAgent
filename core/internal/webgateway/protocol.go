package webgateway

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
)

// WSMessage represents the generic JSON frame sent between browser and WebGateway.
type WSMessage struct {
	Type    string          `json:"type"`              // e.g. "user.text", "agent.text_delta", "agent.emotion"
	Payload json.RawMessage `json:"payload,omitempty"` // Message type specific payload
}

// Inbound User Messages (Browser -> WebGateway)

type UserTextMessagePayload struct {
	Text   string `json:"text"`
	ChatID int64  `json:"chat_id,omitempty"`
}

type UserAudioChunkPayload struct {
	AudioBase64 string `json:"audio_base64"`
	SampleRate  int    `json:"sample_rate,omitempty"`
	Format      string `json:"format,omitempty"`
}

type UserVisionFramePayload struct {
	ImageBase64 string `json:"image_base64"`
	Format      string `json:"format,omitempty"`      // "jpeg" | "webp"
	SourceType  string `json:"source_type,omitempty"` // "screen" | "camera"
	ChatID      int64  `json:"chat_id,omitempty"`
}

// Outbound Agent Messages (WebGateway -> Browser)

type AgentTextDeltaPayload struct {
	Text    string `json:"text"`
	IsFinal bool   `json:"is_final,omitempty"` // true on the last sentence of a reasoning turn; see nats_bridge.go handleActionDecisionMsg
}

type AgentEmotionPayload struct {
	Emotion       string  `json:"emotion"`
	Action        string  `json:"action,omitempty"`
	Mood          string  `json:"mood,omitempty"`
	Valence       float64 `json:"valence,omitempty"`
	Arousal       float64 `json:"arousal,omitempty"`
	Energy        float64 `json:"energy,omitempty"`
	SocialBattery float64 `json:"social_battery,omitempty"`
	Affection     float64 `json:"affection,omitempty"`
	IsJealous     bool    `json:"is_jealous,omitempty"`
	Description   string  `json:"description,omitempty"`
}

type AdminPersonaUpdatePayload struct {
	PersonaID       string `json:"persona_id"`
	Name            string `json:"name,omitempty"`
	Appearance      string `json:"appearance,omitempty"`
	BasePrompt      string `json:"base_prompt,omitempty"`
	SleepyPrompt    string `json:"sleepy_prompt,omitempty"`
	KnowledgeScope  string `json:"knowledge_scope,omitempty"`
	ForbiddenTopics string `json:"forbidden_topics,omitempty"`
}

type AgentAudioChunkPayload struct {
	AudioBase64 string   `json:"audio_base64"`
	SampleRate  int      `json:"sample_rate,omitempty"`
	Format      string   `json:"format,omitempty"`
	Visemes     []Viseme `json:"visemes,omitempty"`
}

type Viseme struct {
	TimeOffset float64 `json:"time_offset"`
	VisemeID   int     `json:"viseme_id"`
	Shape      string  `json:"shape"`
}

type AgentStateChangePayload struct {
	State string `json:"state"` // e.g. "IDLE", "THINKING", "TALKING", "SLEEPING"
}

type AgentSTTTranscriptPayload struct {
	Text    string `json:"text"`
	IsFinal bool   `json:"is_final,omitempty"`
	ChatID  int64  `json:"chat_id,omitempty"`
}

// Binary Audio Protocol (Zero-Copy 0% Base64 Overhead)
// Header (20 Bytes):
// [0..3]: Magic "AUDI"
// [4..11]: ChatID (int64 BigEndian)
// [12..19]: GenerationID (uint64 BigEndian)
// [20..]: Raw Binary Audio Chunk (Opus / PCM)

var AudioMagicHeader = []byte{'A', 'U', 'D', 'I'}

func EncodeBinaryAudioFrame(chatID int64, generationID uint64, rawAudioData []byte) []byte {
	buf := make([]byte, 20+len(rawAudioData))
	copy(buf[0:4], AudioMagicHeader)
	binary.BigEndian.PutUint64(buf[4:12], uint64(chatID))
	binary.BigEndian.PutUint64(buf[12:20], generationID)
	copy(buf[20:], rawAudioData)
	return buf
}

func DecodeBinaryAudioFrame(data []byte) (chatID int64, generationID uint64, rawAudio []byte, err error) {
	if len(data) < 20 {
		return 0, 0, nil, fmt.Errorf("binary frame too short (%d bytes)", len(data))
	}
	if data[0] != 'A' || data[1] != 'U' || data[2] != 'D' || data[3] != 'I' {
		return 0, 0, nil, fmt.Errorf("invalid binary audio magic header")
	}
	chatID = int64(binary.BigEndian.Uint64(data[4:12]))
	generationID = binary.BigEndian.Uint64(data[12:20])
	rawAudio = data[20:]
	return chatID, generationID, rawAudio, nil
}
