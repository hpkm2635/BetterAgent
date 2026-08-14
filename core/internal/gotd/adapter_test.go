package gotd

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/nats-io/nats.go"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"go.uber.org/zap/zaptest/observer"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/engine"
	"betteragent-core/internal/idspace"
	"betteragent-core/internal/schema"
)

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

// TestHandleActionDecision_WebNamespacedChatID_RefusedBeforeAnyTelegramCall
// is the literal PEER_ID_INVALID regression test: with subject-graded
// routing, NATS itself should never deliver a web-channel message to
// GotdAdapter -- but this pins the in-process defense-in-depth guard
// (idspace.IsWebChat) that already caught the real incident, independent of
// routing. The adapter below deliberately leaves antiSpam/humanization/
// typingMgr/sender as nil zero values: if handleActionDecision ever
// proceeded past the IsWebChat guard, the very next lines (typingMgr.
// StopHeartbeat / humanization.CalculateDelay / antiSpam.Wait) would panic
// on a nil receiver -- so "no panic" here is itself proof the guard fired
// before any Telegram-specific call was attempted, not just that a log line
// was printed.
func TestHandleActionDecision_WebNamespacedChatID_RefusedBeforeAnyTelegramCall(t *testing.T) {
	core, logs := observer.New(zapcore.DebugLevel)
	logger := zap.New(core)

	a := &GotdAdapter{
		logger:       logger,
		stateMachine: engine.NewCentralStateMachine(logger),
	}

	webChatID := idspace.WebNamespaceOffset + 4664776 // mirrors the real incident's chat_id
	text := "呜咪，主人真的要把Camelia当成隐形猫猫了吗？"
	a.handleActionDecision(actionDecisionMsg(t, schema.ActionDecisionPayload{
		ChatID:        webChatID,
		SourceChannel: "telegram", // even if mislabeled as telegram, IsWebChat must still win
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
		t.Errorf("expected a hard-safety-violation error log for a WebGateway-namespaced chat_id reaching GotdAdapter")
	}
}

// TestHandleActionDecision_MismatchedSourceChannel_LogsButProceeds pins the
// log-only downgrade for a non-web chat_id whose self-reported
// source_channel disagrees with "telegram" -- it must NOT be dropped
// (subject is authoritative), only logged. Uses IsFinal=false + an
// unrecognized action_type so execution reaches only the lightweight
// stream-buffering default branch, not any real Telegram send call.
func TestHandleActionDecision_MismatchedSourceChannel_LogsButProceeds(t *testing.T) {
	core, logs := observer.New(zapcore.DebugLevel)
	logger := zap.New(core)

	a := &GotdAdapter{
		logger:       logger,
		stateMachine: engine.NewCentralStateMachine(logger),
		antiSpam:     NewAntiSpamGuard(),
		humanization: NewHumanizationEngine(),
		textBuffer:   make(map[int64][]string),
	}
	a.typingMgr = NewTypingHeartbeatManager(a, a.antiSpam, logger)

	text := "hi"
	a.handleActionDecision(actionDecisionMsg(t, schema.ActionDecisionPayload{
		ChatID:        56789, // ordinary Telegram-range chat_id, not web-namespaced
		SourceChannel: "web", // mismatched on purpose
		ActionType:    "unrecognized_type",
		TextContent:   &text,
		IsFinal:       false,
	}))

	found := false
	for _, entry := range logs.All() {
		if entry.Level == zapcore.ErrorLevel {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a mismatch error log when source_channel disagrees with the (implied) telegram subject")
	}
}

func newTestAdapterForGameCommands(t *testing.T) *GotdAdapter {
	t.Helper()
	logger := zap.NewNop()
	natsBus, err := bus.NewNatsBus("nats://127.0.0.1:1", "u", "p", logger)
	if err != nil {
		t.Fatalf("expected NewNatsBus to degrade gracefully to offline mode, got error: %v", err)
	}
	return &GotdAdapter{
		logger:              logger,
		bus:                 natsBus,
		stateMachine:        engine.NewCentralStateMachine(logger),
		autonomousPlayState: engine.NewAutonomousPlayState(),
		// sender left nil deliberately -- replyDirect's nil-guard makes this
		// safe, and it doubles as proof no real Telegram call is attempted.
	}
}

func TestHandleGameStartStopCommand_GameStart_ActivatesAndIntercepts(t *testing.T) {
	a := newTestAdapterForGameCommands(t)
	handled := a.handleGameStartStopCommand(context.Background(), 1001, "/game_start")
	if !handled {
		t.Fatalf("expected /game_start to be intercepted")
	}
	if !a.autonomousPlayState.IsActive() {
		t.Errorf("expected autonomous play to be active after /game_start")
	}
}

func TestHandleGameStartStopCommand_GameStop_DeactivatesAndCancels(t *testing.T) {
	a := newTestAdapterForGameCommands(t)
	a.autonomousPlayState.Activate(1001)

	handled := a.handleGameStartStopCommand(context.Background(), 1001, "/game_stop")
	if !handled {
		t.Fatalf("expected /game_stop to be intercepted")
	}
	if a.autonomousPlayState.IsActive() {
		t.Errorf("expected autonomous play to be inactive after /game_stop")
	}
}

func TestHandleGameStartStopCommand_OrdinaryText_NotIntercepted(t *testing.T) {
	a := newTestAdapterForGameCommands(t)
	if a.handleGameStartStopCommand(context.Background(), 1001, "hello there") {
		t.Errorf("expected ordinary text not to be intercepted as a game command")
	}
	if a.autonomousPlayState.IsActive() {
		t.Errorf("expected autonomous play state untouched by ordinary text")
	}
}

// TestHandleIncomingMessage_GameStartCommand_NeverTouchesNormalPipeline
// confirms /game_start is intercepted before handleIncomingMessage's normal
// pipeline (which would call stateMachine.TransitionToChat(..., StateThinking,
// "inbound_message")) ever runs -- the chat must stay IDLE.
func TestAutonomousPlayState_ActivateDeactivate_ChatStaysIdleUntilPipelineRuns(t *testing.T) {
	a := newTestAdapterForGameCommands(t)
	chatID := int64(1001)

	if got := a.stateMachine.GetChatState(chatID); got != engine.StateIdle {
		t.Fatalf("expected chat to start IDLE, got %s", got)
	}

	a.handleGameStartStopCommand(context.Background(), chatID, "/game_start")

	// handleGameStartStopCommand itself must never transition the CSM --
	// that's the whole point of intercepting before the normal pipeline.
	if got := a.stateMachine.GetChatState(chatID); got != engine.StateIdle {
		t.Errorf("expected chat to remain IDLE after /game_start (command handling must not touch CSM), got %s", got)
	}
}
