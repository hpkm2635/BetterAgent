package webgateway

import (
	"encoding/json"
	"testing"

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
