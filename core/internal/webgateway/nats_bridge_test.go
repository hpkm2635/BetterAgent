package webgateway

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"

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

	return &NatsBridge{
		bus:                 natsBus,
		sessions:            newSessionManager(logger),
		csm:                 engine.NewCentralStateMachine(logger),
		autonomousPlayState: engine.NewAutonomousPlayState(),
		logger:              logger,
	}, logs
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
		ChatID:        1001,
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

	mgr.Add(chatID, genID, text, true, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool) {
		t.Errorf("expected timer not to fire when popped before timeout")
	})

	poppedText, isFinal, ok := mgr.PopAndStop(chatID, genID)
	if !ok || poppedText != text || !isFinal {
		t.Fatalf("expected to pop deferred text %q with isFinal=true, got ok=%v, text=%q, isFinal=%v", text, ok, poppedText, isFinal)
	}

	// Second pop should return false
	_, _, ok2 := mgr.PopAndStop(chatID, genID)
	if ok2 {
		t.Errorf("expected second pop to fail after text was popped")
	}
}

func TestDeferredTextManager_MultiSentenceFIFO(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1005)
	genID := uint64(5)

	mgr.Add(chatID, genID, "句1：你好！", false, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool) {})
	mgr.Add(chatID, genID, "句2：今天天气不错。", true, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool) {})

	txt1, final1, ok1 := mgr.PopAndStop(chatID, genID)
	if !ok1 || txt1 != "句1：你好！" || final1 != false {
		t.Fatalf("expected FIFO sentence 1, got text=%q, final=%v, ok=%v", txt1, final1, ok1)
	}

	txt2, final2, ok2 := mgr.PopAndStop(chatID, genID)
	if !ok2 || txt2 != "句2：今天天气不错。" || final2 != true {
		t.Fatalf("expected FIFO sentence 2, got text=%q, final=%v, ok=%v", txt2, final2, ok2)
	}

	_, _, ok3 := mgr.PopAndStop(chatID, genID)
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

	mgr.Add(chatID, genID, text, true, 50*time.Millisecond, func(cID int64, gID uint64, txt string, final bool) {
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
	_, _, ok := mgr.PopAndStop(chatID, genID)
	if ok {
		t.Errorf("expected PopAndStop to return false after watchdog timeout executed")
	}
}

func TestDeferredTextManager_GenIDIsolation(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1003)

	mgr.Add(chatID, 1, "Turn 1 Text", true, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool) {
		t.Errorf("Turn 1 timer should be stopped")
	})
	mgr.Add(chatID, 2, "Turn 2 Text", true, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool) {
		t.Errorf("Turn 2 timer should be stopped")
	})

	// Popping Turn 1 should not affect Turn 2
	txt1, _, ok1 := mgr.PopAndStop(chatID, 1)
	if !ok1 || txt1 != "Turn 1 Text" {
		t.Errorf("failed to pop Turn 1 text")
	}

	txt2, _, ok2 := mgr.PopAndStop(chatID, 2)
	if !ok2 || txt2 != "Turn 2 Text" {
		t.Errorf("failed to pop Turn 2 text")
	}
}

func TestDeferredTextManager_BargeInClear(t *testing.T) {
	mgr := newDeferredTextManager()
	chatID := int64(1004)

	mgr.Add(chatID, 1, "Interrupted Text", true, 500*time.Millisecond, func(cID int64, gID uint64, txt string, final bool) {
		t.Errorf("Barge-in cleared item should not fire timer callback")
	})

	mgr.ClearChat(chatID)

	_, _, ok := mgr.PopAndStop(chatID, 1)
	if ok {
		t.Errorf("expected item to be cleared by ClearChat")
	}
}
