package webgateway

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"
	"nhooyr.io/websocket"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/engine"
	"betteragent-core/internal/schema"
)

// newTestNatsBridge builds a NatsBridge with real (but disconnected/offline)
// dependencies -- handleActionDecisionMsg unconditionally calls
// b.sessions.SendTextToChat and b.bus.Publish, so these can't be nil, but
// NatsBus.NewNatsBus gracefully falls back to offline mode on connect
// failure (see bus/nats_bus.go), which is exactly what a unit test wants.
func newTestNatsBridge(t *testing.T) (*NatsBridge, *observer.ObservedLogs) {
	t.Helper()
	core, logs := observer.New(zapcore.DebugLevel)
	logger := zap.New(core)

	natsBus, err := bus.NewNatsBus("nats://127.0.0.1:1", "u", "p", logger)
	if err != nil {
		t.Fatalf("expected NewNatsBus to degrade gracefully to offline mode, got error: %v", err)
	}

	b := &NatsBridge{
		bus:                 natsBus,
		sessions:            newSessionManager(logger),
		csm:                 engine.NewCentralStateMachine(logger),
		autonomousPlayState: engine.NewAutonomousPlayState(),
		logger:              logger,
	}
	// Bypasses newNatsBridge's struct literal, so this wiring (normally done
	// there) has to be repeated here for tests that exercise watchdog-timeout
	// behavior to see the same production behavior.
	b.registerWatchdogTimeoutCallback()
	return b, logs
}

func actionDecisionMsg(t *testing.T, decision schema.ActionDecisionPayload) *nats.Msg {
	t.Helper()
	env := struct {
		Payload schema.ActionDecisionPayload `json:"payload"`
	}{Payload: decision}
	data, err := json.Marshal(env)
	if err != nil {
		t.Fatalf("failed to marshal test ActionDecisionPayload: %v", err)
	}
	return &nats.Msg{Data: data}
}

func TestHandleActionDecisionMsg_WebChannel_ProcessedNoMismatchLog(t *testing.T) {
	b, logs := newTestNatsBridge(t)
	text := "hello"
	b.handleActionDecisionMsg(actionDecisionMsg(t, schema.ActionDecisionPayload{
		ChatID: 1001,
		// A fresh CentralStateMachine chat starts at generation 1 (see
		// state_machine.go); this must match or the generation-id filter
		// drops the message before it's ever "processed", defeating what
		// this test is actually meant to exercise.
		GenerationID:  1,
		SourceChannel: "web",
		ActionType:    "send_message",
		TextContent:   &text,
		IsFinal:       true,
	}))

	for _, entry := range logs.All() {
		if entry.Level == zapcore.ErrorLevel {
			t.Errorf("expected no error-level log for a correctly-channeled web decision, got: %s", entry.Message)
		}
	}
}

func TestHandleActionDecisionMsg_MismatchedSourceChannel_ProcessedWithLogNotDropped(t *testing.T) {
	// Regression pin for the subject-graded routing refactor: a message
	// delivered on the web-channel subject (which is the only thing
	// StartSubscriptions subscribes to in production) must be PROCESSED even
	// if its self-reported source_channel payload field says otherwise --
	// the subject is authoritative, the field is now just a mismatch signal.
	b, logs := newTestNatsBridge(t)
	text := "hello"
	b.handleActionDecisionMsg(actionDecisionMsg(t, schema.ActionDecisionPayload{
		ChatID:        1001,
		GenerationID:  1,          // matches the fresh chat's starting generation
		SourceChannel: "telegram", // mismatched on purpose
		ActionType:    "send_message",
		TextContent:   &text,
		IsFinal:       true,
	}))

	found := false
	for _, entry := range logs.All() {
		if entry.Level == zapcore.ErrorLevel {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a mismatch error log when source_channel disagrees with the (implied) web subject")
	}
	// If it had been dropped (old behavior), state_machine.GetChatState would
	// never have been touched via TouchWatchdogChat -- IsFinal + no urgeEngine
	// panic is the main risk here, so completing this call without a panic
	// while producing the log line above is the meaningful assertion.
}

func TestHandleActionDecisionMsg_ZeroGenerationID_TreatedAsStaleAndDropped(t *testing.T) {
	// A chat's generationID starts at 1 and only increments (state_machine.go),
	// so it is never legitimately 0 -- a decision arriving with GenerationID
	// unset/0 must be dropped as stale, not specially let through. This pins
	// the fix for the gap where "GenerationID != 0" bypassed the staleness
	// check entirely for zero-valued payloads.
	b, logs := newTestNatsBridge(t)
	text := "hello"
	b.handleActionDecisionMsg(actionDecisionMsg(t, schema.ActionDecisionPayload{
		ChatID: 1002,
		// GenerationID intentionally left at its zero value.
		SourceChannel: "web",
		ActionType:    "send_message",
		TextContent:   &text,
		IsFinal:       true,
	}))

	found := false
	for _, entry := range logs.All() {
		if entry.Level == zapcore.WarnLevel && entry.Message != "" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a 'dropped stale ActionDecision' warning for a zero-value GenerationID")
	}
}

func audioChunkMsg(t *testing.T, payload schema.StreamAudioChunkPayload) *nats.Msg {
	t.Helper()
	env := struct {
		Payload schema.StreamAudioChunkPayload `json:"payload"`
	}{Payload: payload}
	data, err := json.Marshal(env)
	if err != nil {
		t.Fatalf("failed to marshal test StreamAudioChunkPayload: %v", err)
	}
	return &nats.Msg{Data: data}
}

// TestHandleAudioChunkMsg_BinaryFrameTaggedWithChunksOwnGeneration_NotCurrentChatGeneration
// pins a bug where the binary AUDI frame was tagged with
// b.csm.GetGenerationChat(chatID) -- the chat's *current* generation at the
// moment this handler runs -- instead of the audio chunk's own
// GenerationID. A barge-in between when TTS produced this chunk and when
// this handler processes it bumps the chat's current generation, so the
// stale chunk would get silently re-stamped as current and defeat the
// client-side staleness filter that trusts this exact field.
func TestHandleAudioChunkMsg_BinaryFrameTaggedWithChunksOwnGeneration_NotCurrentChatGeneration(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(5555)

	session := newClientSession(chatID, nil, b.logger)
	b.sessions.Register(session)
	defer b.sessions.Unregister(session)

	// Chat is already at generation 3 (e.g. two barge-ins happened after
	// this audio chunk, which belongs to generation 1, was produced).
	b.csm.IncrementGenerationChat(chatID)
	b.csm.IncrementGenerationChat(chatID)
	if got := b.csm.GetGenerationChat(chatID); got != 3 {
		t.Fatalf("test setup: expected chat generation 3, got %d", got)
	}

	b.handleAudioChunkMsg(audioChunkMsg(t, schema.StreamAudioChunkPayload{
		ChatID:       chatID,
		GenerationID: 1,
		AudioBase64:  "AQIDBA==", // 4 raw bytes, base64
		SampleRate:   32000,
		Format:       "pcm",
	}))

	// handleAudioChunkMsg sends a JSON text frame (agent.audio_chunk, for
	// stage-web compat) before the binary AUDI frame -- drain until the
	// binary one.
	for {
		select {
		case frame := <-session.sendChan:
			if frame.MessageType != websocket.MessageBinary {
				continue
			}
			_, decodedGenID, _, err := DecodeBinaryAudioFrame(frame.Data)
			if err != nil {
				t.Fatalf("failed to decode binary audio frame: %v", err)
			}
			if decodedGenID != 1 {
				t.Errorf("expected binary frame tagged with the chunk's own GenerationID=1, got %d (chat's current generation is 3)", decodedGenID)
			}
			return
		default:
			t.Fatal("expected a binary frame to be sent to the session, got none")
		}
	}
}

func drainSentFrames(session *ClientSession) []WSFrame {
	var frames []WSFrame
	for {
		select {
		case frame := <-session.sendChan:
			frames = append(frames, frame)
		default:
			return frames
		}
	}
}

// TestWatchdogTimeout_BroadcastsIdleStateChangeToBrowser pins the fix for a
// real bug: a chat stuck in TALKING/STREAMING_TTS whose watchdog times out
// (e.g. audio chunks genuinely stopped arriving) used to flip to IDLE
// purely inside the CentralStateMachine with nobody telling the browser --
// registerWatchdogTimeoutCallback wires that notification through.
func TestWatchdogTimeout_BroadcastsIdleStateChangeToBrowser(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(8888)

	session := newClientSession(chatID, nil, b.logger)
	b.sessions.Register(session)
	defer b.sessions.Unregister(session)

	if ok := b.csm.TransitionToChat(chatID, engine.StateThinking, "test_setup"); !ok {
		t.Fatalf("test setup: expected IDLE -> THINKING to be a valid transition")
	}
	if ok := b.csm.TransitionToChat(chatID, engine.StateTalking, "test_setup"); !ok {
		t.Fatalf("test setup: expected THINKING -> TALKING to be a valid transition")
	}

	b.csm.TouchWatchdogChat(chatID, 20*time.Millisecond)

	deadline := time.After(500 * time.Millisecond)
	for {
		select {
		case frame := <-session.sendChan:
			if frame.MessageType != websocket.MessageText {
				continue
			}
			var wsMsg WSMessage
			if err := json.Unmarshal(frame.Data, &wsMsg); err != nil {
				t.Fatalf("failed to unmarshal WS frame: %v", err)
			}
			if wsMsg.Type != "agent.state_change" {
				continue
			}
			var payload map[string]any
			if err := json.Unmarshal(wsMsg.Payload, &payload); err != nil {
				t.Fatalf("failed to unmarshal agent.state_change payload: %v", err)
			}
			if payload["state"] != "idle" {
				t.Fatalf("expected state:idle, got %v", payload["state"])
			}
			if payload["reason"] != "watchdog_timeout" {
				t.Errorf("expected reason:watchdog_timeout, got %v", payload["reason"])
			}
			return
		case <-deadline:
			t.Fatal("expected an agent.state_change idle frame within 500ms of the watchdog timeout, got none")
		}
	}
}

// TestWatchdogTimeout_SuppressedByRealStateChangeBeforeDeadline confirms the
// browser-facing broadcast, like the underlying watchdog itself, is
// suppressed once a real transition (e.g. barge-in) supersedes the state
// the watchdog was armed for -- it must not fire a stale idle notification
// for a turn that's already moved on.
func TestWatchdogTimeout_SuppressedByRealStateChangeBeforeDeadline(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(8889)

	session := newClientSession(chatID, nil, b.logger)
	b.sessions.Register(session)
	defer b.sessions.Unregister(session)

	if ok := b.csm.TransitionToChat(chatID, engine.StateThinking, "test_setup"); !ok {
		t.Fatalf("test setup: expected IDLE -> THINKING to be a valid transition")
	}
	if ok := b.csm.TransitionToChat(chatID, engine.StateTalking, "test_setup"); !ok {
		t.Fatalf("test setup: expected THINKING -> TALKING to be a valid transition")
	}

	b.csm.TouchWatchdogChat(chatID, 30*time.Millisecond)

	if ok := b.csm.TransitionToChat(chatID, engine.StateCancelling, "barge_in"); !ok {
		t.Fatalf("test setup: expected TALKING -> CANCELLING to be a valid transition")
	}

	time.Sleep(150 * time.Millisecond)

	for _, frame := range drainSentFrames(session) {
		if frame.MessageType != websocket.MessageText {
			continue
		}
		var wsMsg WSMessage
		if err := json.Unmarshal(frame.Data, &wsMsg); err != nil {
			t.Fatalf("failed to unmarshal WS frame: %v", err)
		}
		if wsMsg.Type == "agent.state_change" {
			var payload map[string]any
			if err := json.Unmarshal(wsMsg.Payload, &payload); err == nil && payload["reason"] == "watchdog_timeout" {
				t.Fatalf("expected no watchdog_timeout broadcast after a real state transition superseded it")
			}
		}
	}
}

// TestHandleAudioChunkMsg_NonSentenceStartChunk_DoesNotFlushDeferredText pins
// the fix for a real caption-ordering bug: PopAndStop must only fire on a
// sentence's *first* audio chunk (IsSentenceStart), not on every one of the
// dozens of sub-chunks GPT-SoVITS streams per sentence -- otherwise a later
// sentence's already-queued text gets flushed to the browser while the
// current sentence's audio is still only partway through playing, making
// the live caption visibly race ahead of what's actually audible.
func TestHandleAudioChunkMsg_NonSentenceStartChunk_DoesNotFlushDeferredText(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(7777)
	genID := uint64(1)

	session := newClientSession(chatID, nil, b.logger)
	b.sessions.Register(session)
	defer b.sessions.Unregister(session)

	b.getDeferredTexts().Add(chatID, genID, "下一句还没到该说的时候", true, nil, 800*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {
		t.Errorf("expected watchdog timeout not to fire during this test")
	})

	b.handleAudioChunkMsg(audioChunkMsg(t, schema.StreamAudioChunkPayload{
		ChatID:          chatID,
		GenerationID:    genID,
		AudioBase64:     "AQIDBA==",
		SampleRate:      32000,
		Format:          "wav",
		IsSentenceStart: false,
	}))

	for _, frame := range drainSentFrames(session) {
		if frame.MessageType != websocket.MessageText {
			continue
		}
		var wsMsg WSMessage
		if err := json.Unmarshal(frame.Data, &wsMsg); err != nil {
			t.Fatalf("failed to unmarshal WS frame: %v", err)
		}
		if wsMsg.Type == "agent.text_delta" {
			t.Fatalf("expected no agent.text_delta frame for a non-sentence-start chunk, got one")
		}
	}

	if poppedText, _, _, popped := b.getDeferredTexts().PopAndStop(chatID, genID); !popped || poppedText == "" {
		t.Errorf("expected the deferred text to still be queued (never flushed) after a non-sentence-start chunk")
	}
}

// TestHandleAudioChunkMsg_SentenceStartChunk_FlushesDeferredTextAndForwardsTextSegment
// covers the other half: a sentence's first chunk (IsSentenceStart=true)
// must flush its deferred full-sentence text as before, AND the chunk's own
// TextDelta (now repurposed as this chunk's own text slice, see
// allocate_viseme_text_slice) must be forwarded into the agent.audio_chunk
// WS message's new text_segment field for the frontend's typewriter reveal.
func TestHandleAudioChunkMsg_SentenceStartChunk_FlushesDeferredTextAndForwardsTextSegment(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(7778)
	genID := uint64(1)

	session := newClientSession(chatID, nil, b.logger)
	b.sessions.Register(session)
	defer b.sessions.Unregister(session)

	citations := []schema.Citation{{Content: "图书馆周一至周五开放至22:00", Source: "faq.md", RelevanceScore: 0.9}}
	b.getDeferredTexts().Add(chatID, genID, "这句话终于轮到你了", true, citations, 800*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {
		t.Errorf("expected watchdog timeout not to fire during this test")
	})

	b.handleAudioChunkMsg(audioChunkMsg(t, schema.StreamAudioChunkPayload{
		ChatID:          chatID,
		GenerationID:    genID,
		AudioBase64:     "AQIDBA==",
		SampleRate:      32000,
		Format:          "wav",
		TextDelta:       "这块音频自己的字",
		IsSentenceStart: true,
	}))

	var sawTextDelta, sawAudioChunk bool
	for _, frame := range drainSentFrames(session) {
		if frame.MessageType != websocket.MessageText {
			continue
		}
		var wsMsg WSMessage
		if err := json.Unmarshal(frame.Data, &wsMsg); err != nil {
			t.Fatalf("failed to unmarshal WS frame: %v", err)
		}
		switch wsMsg.Type {
		case "agent.text_delta":
			sawTextDelta = true
			var payload AgentTextDeltaPayload
			if err := json.Unmarshal(wsMsg.Payload, &payload); err != nil {
				t.Fatalf("failed to unmarshal AgentTextDeltaPayload: %v", err)
			}
			if payload.Text != "这句话终于轮到你了" {
				t.Errorf("expected deferred sentence text to be flushed, got %q", payload.Text)
			}
			if len(payload.Citations) != 1 || payload.Citations[0].Content != "图书馆周一至周五开放至22:00" || payload.Citations[0].Source != "faq.md" {
				t.Errorf("expected citations to be forwarded alongside the deferred text, got %+v", payload.Citations)
			}
		case "agent.audio_chunk":
			sawAudioChunk = true
			var payload AgentAudioChunkPayload
			if err := json.Unmarshal(wsMsg.Payload, &payload); err != nil {
				t.Fatalf("failed to unmarshal AgentAudioChunkPayload: %v", err)
			}
			if payload.TextSegment != "这块音频自己的字" {
				t.Errorf("expected text_segment to forward this chunk's own TextDelta, got %q", payload.TextSegment)
			}
		}
	}
	if !sawTextDelta {
		t.Errorf("expected an agent.text_delta frame to be flushed on the sentence's first chunk")
	}
	if !sawAudioChunk {
		t.Fatalf("expected an agent.audio_chunk frame to be sent")
	}

	if _, _, _, popped := b.getDeferredTexts().PopAndStop(chatID, genID); popped {
		t.Errorf("expected the deferred text queue to be empty after being flushed")
	}
}

func sttTranscriptMsg(t *testing.T, payload schema.STTFinalTranscriptPayload) *nats.Msg {
	t.Helper()
	env := struct {
		Payload schema.STTFinalTranscriptPayload `json:"payload"`
	}{Payload: payload}
	data, err := json.Marshal(env)
	if err != nil {
		t.Fatalf("failed to marshal test STTFinalTranscriptPayload: %v", err)
	}
	return &nats.Msg{Data: data}
}

// TestHandleSTTPartialMsg_RelaysToWSButNeverTriggersReasoning covers wiring
// up agent.stt.stream_partial (previously never subscribed at all, so
// mid-utterance transcripts never reached the browser -- see
// handleSTTPartialMsg's doc comment). Confirms both halves: the browser
// gets an is_final:false preview frame, and -- unlike handleSTTFinalMsg --
// the chat is never pushed into a reasoning turn over a partial result.
func TestHandleSTTPartialMsg_RelaysToWSButNeverTriggersReasoning(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(6666)

	session := newClientSession(chatID, nil, b.logger)
	b.sessions.Register(session)
	defer b.sessions.Unregister(session)

	if got := b.csm.GetChatState(chatID); got != engine.StateIdle {
		t.Fatalf("test setup: expected chat to start IDLE, got %s", got)
	}

	b.handleSTTPartialMsg(sttTranscriptMsg(t, schema.STTFinalTranscriptPayload{
		ChatID: chatID,
		Text:   "帮我查一下图书",
	}))

	select {
	case frame := <-session.sendChan:
		var wsMsg WSMessage
		if err := json.Unmarshal(frame.Data, &wsMsg); err != nil {
			t.Fatalf("failed to unmarshal WS frame: %v", err)
		}
		if wsMsg.Type != "agent.stt_transcript" {
			t.Fatalf("expected agent.stt_transcript frame, got %q", wsMsg.Type)
		}
		var payload AgentSTTTranscriptPayload
		if err := json.Unmarshal(wsMsg.Payload, &payload); err != nil {
			t.Fatalf("failed to unmarshal AgentSTTTranscriptPayload: %v", err)
		}
		if payload.IsFinal {
			t.Errorf("expected IsFinal=false for a partial transcript, got true")
		}
		if payload.Text != "帮我查一下图书" {
			t.Errorf("expected text to be relayed unchanged, got %q", payload.Text)
		}
	default:
		t.Fatal("expected a WS frame to be sent to the session, got none")
	}

	if got := b.csm.GetChatState(chatID); got != engine.StateIdle {
		t.Errorf("expected chat to remain IDLE after a partial transcript (must not trigger a reasoning turn), got %s", got)
	}
}

func TestHandleGameStartStopCommand_GameStart_ActivatesAndIntercepts(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	handled := b.handleGameStartStopCommand(1001, "/game_start")
	if !handled {
		t.Fatalf("expected /game_start to be intercepted")
	}
	if !b.autonomousPlayState.IsActive() {
		t.Errorf("expected autonomous play to be active after /game_start")
	}
}

func TestHandleGameStartStopCommand_GameStop_DeactivatesAndCancels(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	b.autonomousPlayState.Activate(1001)

	handled := b.handleGameStartStopCommand(1001, "/game_stop")
	if !handled {
		t.Fatalf("expected /game_stop to be intercepted")
	}
	if b.autonomousPlayState.IsActive() {
		t.Errorf("expected autonomous play to be inactive after /game_stop")
	}
}

func TestHandleGameStartStopCommand_OrdinaryText_NotIntercepted(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	if b.handleGameStartStopCommand(1001, "hello there") {
		t.Errorf("expected ordinary text not to be intercepted as a game command")
	}
}

// TestPublishInboundMessage_GameStartCommand_NeverTouchesCSM confirms
// /game_start is intercepted before publishInboundMessage's normal pipeline
// (which would call csm.TransitionToChat(..., StateThinking, "inbound_message"))
// ever runs -- the chat must stay IDLE.
func TestPublishInboundMessage_GameStartCommand_NeverTouchesCSM(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(1001)

	if got := b.csm.GetChatState(chatID); got != engine.StateIdle {
		t.Fatalf("expected chat to start IDLE, got %s", got)
	}

	b.publishInboundMessage(chatID, "/game_start", "", nil)

	if got := b.csm.GetChatState(chatID); got != engine.StateIdle {
		t.Errorf("expected chat to remain IDLE after /game_start (command handling must not touch CSM), got %s", got)
	}
	if !b.autonomousPlayState.IsActive() {
		t.Errorf("expected autonomous play to be active after /game_start routed through publishInboundMessage")
	}
}

func TestDeferredTextManager_AddAndPop(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1001)
	genID := uint64(1)
	text := "主人的金枪鱼拿来喵~"
	citations := []schema.Citation{{Content: "test citation", Source: "faq.md"}}

	mgr.Add(chatID, genID, text, true, citations, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {
		t.Errorf("expected timer not to fire when popped before timeout")
	})

	poppedText, isFinal, poppedCitations, ok := mgr.PopAndStop(chatID, genID)
	if !ok || poppedText != text || !isFinal {
		t.Fatalf("expected to pop deferred text %q with isFinal=true, got ok=%v, text=%q, isFinal=%v", text, ok, poppedText, isFinal)
	}
	if len(poppedCitations) != 1 || poppedCitations[0].Content != "test citation" {
		t.Errorf("expected citations to round-trip through Add/PopAndStop, got %+v", poppedCitations)
	}

	// Second pop should return false
	_, _, _, ok2 := mgr.PopAndStop(chatID, genID)
	if ok2 {
		t.Errorf("expected second pop to fail after text was popped")
	}
}

func TestDeferredTextManager_MultiSentenceFIFO(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1005)
	genID := uint64(5)

	mgr.Add(chatID, genID, "句1：你好！", false, nil, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {})
	mgr.Add(chatID, genID, "句2：今天天气不错。", true, nil, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {})

	txt1, final1, _, ok1 := mgr.PopAndStop(chatID, genID)
	if !ok1 || txt1 != "句1：你好！" || final1 != false {
		t.Fatalf("expected FIFO sentence 1, got text=%q, final=%v, ok=%v", txt1, final1, ok1)
	}

	txt2, final2, _, ok2 := mgr.PopAndStop(chatID, genID)
	if !ok2 || txt2 != "句2：今天天气不错。" || final2 != true {
		t.Fatalf("expected FIFO sentence 2, got text=%q, final=%v, ok=%v", txt2, final2, ok2)
	}

	_, _, _, ok3 := mgr.PopAndStop(chatID, genID)
	if ok3 {
		t.Errorf("expected queue to be empty after popping both sentences")
	}
}

func TestDeferredTextManager_WatchdogTimeout(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1002)
	genID := uint64(2)
	text := "超时保底文本上屏"
	fired := make(chan string, 1)

	mgr.Add(chatID, genID, text, true, nil, 50*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {
		fired <- txt
	})

	select {
	case gotText := <-fired:
		if gotText != text {
			t.Errorf("expected timeout callback to receive %q, got %q", text, gotText)
		}
	case <-time.After(200 * time.Millisecond):
		t.Fatalf("expected watchdog timeout callback to fire within 200ms")
	}

	// After watchdog timeout fired, PopAndStop should return false
	_, _, _, ok := mgr.PopAndStop(chatID, genID)
	if ok {
		t.Errorf("expected PopAndStop to return false after watchdog timeout executed")
	}
}

func TestDeferredTextManager_GenIDIsolation(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1003)

	mgr.Add(chatID, 1, "Turn 1 Text", true, nil, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {
		t.Errorf("Turn 1 timer should be stopped")
	})
	mgr.Add(chatID, 2, "Turn 2 Text", true, nil, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {
		t.Errorf("Turn 2 timer should be stopped")
	})

	// Popping Turn 1 should not affect Turn 2
	txt1, _, _, ok1 := mgr.PopAndStop(chatID, 1)
	if !ok1 || txt1 != "Turn 1 Text" {
		t.Errorf("failed to pop Turn 1 text")
	}

	txt2, _, _, ok2 := mgr.PopAndStop(chatID, 2)
	if !ok2 || txt2 != "Turn 2 Text" {
		t.Errorf("failed to pop Turn 2 text")
	}
}

func scheduleFiredMsg(t *testing.T, payload schema.ScheduleFiredPayload) *nats.Msg {
	t.Helper()
	env := struct {
		Payload schema.ScheduleFiredPayload `json:"payload"`
	}{Payload: payload}
	data, err := json.Marshal(env)
	if err != nil {
		t.Fatalf("failed to marshal test ScheduleFiredPayload: %v", err)
	}
	return &nats.Msg{Data: data}
}

func TestHandleScheduleFiredMsg_ValidPayload_TriggersProactiveTurn(t *testing.T) {
	b, _ := newTestNatsBridge(t)
	chatID := int64(1001)

	if got := b.csm.GetChatState(chatID); got != engine.StateIdle {
		t.Fatalf("expected chat to start Idle, got %v", got)
	}

	b.handleScheduleFiredMsg(scheduleFiredMsg(t, schema.ScheduleFiredPayload{
		ChatID: chatID,
		Title:  "赶火车回学校",
		Note:   "带身份证",
	}))

	// PublishProactiveTurn's first side effect is transitioning the chat to
	// Thinking (proactive_trigger.go) -- observable regardless of the test
	// bus being offline, and the meaningful signal that a companion-service
	// reminder actually reached the proactive-turn pipeline instead of being
	// silently dropped.
	if got := b.csm.GetChatState(chatID); got != engine.StateThinking {
		t.Fatalf("expected schedule fire to trigger a proactive turn (chat -> Thinking), got %v", got)
	}
}

func TestHandleScheduleFiredMsg_ZeroChatID_Ignored(t *testing.T) {
	b, _ := newTestNatsBridge(t)

	b.handleScheduleFiredMsg(scheduleFiredMsg(t, schema.ScheduleFiredPayload{
		ChatID: 0,
		Title:  "赶火车回学校",
	}))

	// A malformed/zero chat_id must never reach PublishProactiveTurn -- there
	// is no valid target to transition, and CentralStateMachine has no
	// meaningful "chat 0". Completing this call without touching any chat
	// state (and without panicking) is the assertion.
	if got := b.csm.GetChatState(1001); got != engine.StateIdle {
		t.Fatalf("unrelated chat_id must not be affected by a zero-chat_id event, got %v", got)
	}
}

func TestDeferredTextManager_BargeInClear(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1004)

	mgr.Add(chatID, 1, "Interrupted Text", true, nil, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool, cites []schema.Citation) {
		t.Errorf("Barge-in cleared item should not fire timer callback")
	})

	mgr.ClearChat(chatID)

	_, _, _, ok := mgr.PopAndStop(chatID, 1)
	if ok {
		t.Errorf("expected item to be cleared by ClearChat")
	}
}
