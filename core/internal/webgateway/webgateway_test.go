package webgateway

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"

	"go.uber.org/zap"
)

func TestWSMessageMarshalUnmarshal(t *testing.T) {
	textPayload := UserTextMessagePayload{
		Text:   "Hello Neko~",
		ChatID: 1001,
	}

	payloadBytes, err := json.Marshal(textPayload)
	if err != nil {
		t.Fatalf("failed to marshal UserTextMessagePayload: %v", err)
	}

	wsMsg := WSMessage{
		Type:    "user.text",
		Payload: payloadBytes,
	}

	msgBytes, err := json.Marshal(wsMsg)
	if err != nil {
		t.Fatalf("failed to marshal WSMessage: %v", err)
	}

	var decodedWS WSMessage
	if err := json.Unmarshal(msgBytes, &decodedWS); err != nil {
		t.Fatalf("failed to unmarshal WSMessage: %v", err)
	}

	if decodedWS.Type != "user.text" {
		t.Errorf("expected type 'user.text', got '%s'", decodedWS.Type)
	}

	var decodedPayload UserTextMessagePayload
	if err := json.Unmarshal(decodedWS.Payload, &decodedPayload); err != nil {
		t.Fatalf("failed to unmarshal payload: %v", err)
	}

	if decodedPayload.Text != "Hello Neko~" || decodedPayload.ChatID != 1001 {
		t.Errorf("decoded payload mismatch: %+v", decodedPayload)
	}
}

func TestDynamicChatIDResolution(t *testing.T) {
	// Test 1: URL with explicit chat_id parameter is folded into the web namespace,
	// so it can never collide with a real Telegram chat/user ID.
	reqWithQuery := httptest.NewRequest("GET", "http://localhost:8080/ws?chat_id=2024", nil)
	chatID1 := parseOrGenerateChatID(reqWithQuery)
	if chatID1 != WebNamespaceOffset+2024 {
		t.Errorf("expected parsed chat_id %d, got %d", WebNamespaceOffset+2024, chatID1)
	}

	// Test 2: URL without chat_id parameter generates unique ID
	reqNoQuery1 := httptest.NewRequest("GET", "http://localhost:8080/ws", nil)
	reqNoQuery2 := httptest.NewRequest("GET", "http://localhost:8080/ws", nil)
	chatIDGen1 := parseOrGenerateChatID(reqNoQuery1)
	chatIDGen2 := parseOrGenerateChatID(reqNoQuery2)

	if chatIDGen1 <= 0 || chatIDGen2 <= 0 {
		t.Errorf("generated chat_ids must be positive numbers, got %d and %d", chatIDGen1, chatIDGen2)
	}

	if chatIDGen1 == chatIDGen2 {
		t.Errorf("generated chat_ids for different sessions should be unique, got collision: %d", chatIDGen1)
	}
}

func TestBargeInSendBufferPurge(t *testing.T) {
	logger := zap.NewNop()
	session := newClientSession(1001, nil, logger)

	// Fill send buffer with 5 text frames
	for i := 0; i < 5; i++ {
		session.SendText([]byte("stale audio chunk"))
	}

	if len(session.sendChan) != 5 {
		t.Fatalf("expected send buffer size 5, got %d", len(session.sendChan))
	}

	initialGenID := session.GetGenerationID()

	// Perform Barge-in Clear
	session.ClearSendBuffer()

	if len(session.sendChan) != 0 {
		t.Errorf("expected send buffer size 0 after Barge-in clear, got %d", len(session.sendChan))
	}

	if session.GetGenerationID() <= initialGenID {
		t.Errorf("generation ID should increment on Barge-in clear, before=%d, after=%d", initialGenID, session.GetGenerationID())
	}
}

func TestBinaryAudioFrameEncoding(t *testing.T) {
	chatID := int64(9876543210)
	genID := uint64(42)
	pcmAudio := []byte{0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08}

	encoded := EncodeBinaryAudioFrame(chatID, genID, pcmAudio)

	decodedChatID, decodedGenID, decodedAudio, err := DecodeBinaryAudioFrame(encoded)
	if err != nil {
		t.Fatalf("failed to decode binary audio frame: %v", err)
	}

	if decodedChatID != chatID {
		t.Errorf("expected chatID %d, got %d", chatID, decodedChatID)
	}

	if decodedGenID != genID {
		t.Errorf("expected genID %d, got %d", genID, decodedGenID)
	}

	if !bytes.Equal(decodedAudio, pcmAudio) {
		t.Errorf("expected audio bytes %v, got %v", pcmAudio, decodedAudio)
	}
}

func TestSessionManagerIsolatedChat(t *testing.T) {
	logger := zap.NewNop()
	sm := newSessionManager(logger)

	sessionA := newClientSession(1001, nil, logger)
	sessionB := newClientSession(2002, nil, logger)

	sm.Register(sessionA)
	sm.Register(sessionB)
	defer sm.Unregister(sessionA)
	defer sm.Unregister(sessionB)

	// Send 3 frames to Chat 1001
	for i := 0; i < 3; i++ {
		sm.SendTextToChat(1001, []byte("msg for 1001"))
	}

	time.Sleep(10 * time.Millisecond)

	if len(sessionA.sendChan) != 3 {
		t.Errorf("sessionA should receive 3 messages, got %d", len(sessionA.sendChan))
	}

	if len(sessionB.sendChan) != 0 {
		t.Errorf("sessionB (Chat 2002) should receive 0 messages for Chat 1001, got %d", len(sessionB.sendChan))
	}
}
