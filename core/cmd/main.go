package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.uber.org/zap"

	"betteragent-core/internal/bus"
	"betteragent-core/internal/config"
	"betteragent-core/internal/emotion"
	"betteragent-core/internal/engine"
	"betteragent-core/internal/gotd"
	"betteragent-core/internal/webgateway"
)

func main() {
	_ = os.MkdirAll("../logs", 0755)
	_ = os.MkdirAll("logs", 0755)

	zapCfg := zap.NewProductionConfig()
	zapCfg.Level = zap.NewAtomicLevelAt(zap.InfoLevel)
	zapCfg.OutputPaths = []string{"stdout", "../logs/betteragent_core.log"}

	logger, err := zapCfg.Build()
	if err != nil {
		logger, _ = zap.NewProduction()
	}
	defer logger.Sync()

	logger.Info("Starting BetterAgent Core (Go)...")

	cfg := config.LoadConfig()

	if cfg.NatsUser == "" || cfg.NatsPassword == "" {
		logger.Fatal("NATS_USER / NATS_PASSWORD are not set. Refusing to start with an unauthenticated message bus (see .env.example).")
	}
	if cfg.WebGatewayToken == "" {
		logger.Fatal("WEBGATEWAY_TOKEN is not set. Refusing to start an unauthenticated WebGateway WebSocket endpoint (see .env.example).")
	}

	// Initialize NATS Bus
	natsBus, err := bus.NewNatsBus(cfg.NatsURL, cfg.NatsUser, cfg.NatsPassword, logger)
	if err != nil {
		logger.Fatal("Failed to connect to NATS", zap.Error(err))
	}
	defer natsBus.Close()

	// Initialize Emotional & Personality System
	emoState := emotion.NewEmotionalState()
	personality := emotion.DefaultPersonality()
	circadian := emotion.NewCircadianRhythmEvaluator()

	// Initialize CentralStateMachine
	csm := engine.NewCentralStateMachine(logger)

	// Initialize UrgeEngine (cognitive impulse to speak proactively -- see
	// core/internal/engine/urge_engine.go and docs/ARCHITECTURE.md)
	urgeYAML := cfg.YAML.CoreEngine.Urge
	urgeEngine := engine.NewUrgeEngine(engine.UrgeParams{
		AlphaBoredom:         urgeYAML.AlphaBoredom,
		BetaGameEvent:        urgeYAML.BetaGameEvent,
		GammaUnreadPressure:  urgeYAML.GammaUnreadPressure,
		BaseThreshold:        urgeYAML.BaseThreshold,
		ArousalSensitivity:   urgeYAML.ArousalSensitivity,
		EnergyPenalty:        urgeYAML.EnergyPenalty,
		MinThreshold:         urgeYAML.MinThreshold,
		MaxThreshold:         urgeYAML.MaxThreshold,
		UrgeCap:              urgeYAML.UrgeCap,
		CooldownDuration:     time.Duration(urgeYAML.CooldownSeconds) * time.Second,
		DeadZoneDuration:     time.Duration(urgeYAML.DeadZoneSeconds) * time.Second,
		GameEventDecay:       time.Duration(urgeYAML.GameEventDecaySeconds) * time.Second,
		UnreadPressureWindow: time.Duration(urgeYAML.UnreadPressureWindowSeconds) * time.Second,
		PrimaryChatID:        urgeYAML.PrimaryChatID,
		TargetSessionMaxAge:  time.Duration(urgeYAML.TargetSessionMaxAgeSeconds) * time.Second,
	}, logger)

	// Autonomous game play toggle (see /game_start //game_stop handling in
	// gotd/adapter.go and webgateway/nats_bridge.go, and POST /api/game-turn
	// in webgateway/game_turn_handler.go). Default OFF -- nothing fires
	// until a human explicitly activates it.
	autonomousPlayState := engine.NewAutonomousPlayState()

	// Initialize ClockEngine (30s interval tick)
	clockEngine := engine.NewClockEngine(30*time.Second, natsBus, csm, emoState, personality, circadian, urgeEngine, autonomousPlayState, logger)

	gameEventWeights := webgateway.GameEventWeights{
		DefaultWeight: cfg.YAML.GameEvents.DefaultWeight,
		Games:         cfg.YAML.GameEvents.Games,
	}

	// Initialize WebGateway WebSocket Server (Port 8080) + dedicated
	// loopback-only game-event listener (see game_event_bind_addr in config.yaml)
	webServer := webgateway.NewServer(":8080", cfg.WebGatewayToken, cfg.WebGatewayAllowedOrigins, natsBus, csm, emoState, personality, circadian, urgeEngine, autonomousPlayState, cfg.GameEventToken, cfg.YAML.CoreEngine.GameEventBindAddr, gameEventWeights, logger)
	if err := webServer.Start(); err != nil {
		logger.Error("Failed to start WebGateway server", zap.Error(err))
	}
	defer func() {
		stopCtx, stopCancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer stopCancel()
		_ = webServer.Stop(stopCtx)
	}()

	// Initialize GotdAdapter
	adapter, err := gotd.NewGotdAdapter(cfg, natsBus, csm, emoState, personality, circadian, urgeEngine, autonomousPlayState, logger)
	if err != nil {
		logger.Fatal("Failed to create GotdAdapter", zap.Error(err))
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start ClockEngine
	clockEngine.Start(ctx)

	// Initialize & Start EmotionDeltaHandler for NATS agent.emotion.delta updates
	emotionDeltaHandler := engine.NewEmotionDeltaHandler(natsBus, clockEngine.GetEmotionStore(), logger)
	if err := emotionDeltaHandler.Start(); err != nil {
		logger.Error("Failed to start EmotionDeltaHandler", zap.Error(err))
	}
	webServer.SetEmotionStore(clockEngine.GetEmotionStore())

	// Handle graceful shutdown signals
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		sig := <-sigChan
		logger.Info("Shutdown signal received", zap.String("signal", sig.String()))
		cancel()
	}()

	logger.Info("BetterAgent Core components initialized successfully. Ready for NATS & Telegram IO.")

	// Start GotdAdapter loop (blocks until context canceled)
	if err := adapter.Start(ctx); err != nil {
		logger.Info("GotdAdapter execution ended", zap.Error(err))
	}
}
