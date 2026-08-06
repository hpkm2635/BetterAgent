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

	// Initialize NATS Bus
	natsBus, err := bus.NewNatsBus(cfg.NatsURL, logger)
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

	// Initialize ClockEngine (30s interval tick)
	clockEngine := engine.NewClockEngine(30*time.Second, natsBus, csm, emoState, circadian, logger)

	// Initialize WebGateway WebSocket Server (Port 8080)
	webServer := webgateway.NewServer(":8080", natsBus, csm, emoState, personality, circadian, logger)
	if err := webServer.Start(); err != nil {
		logger.Error("Failed to start WebGateway server", zap.Error(err))
	}
	defer func() {
		stopCtx, stopCancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer stopCancel()
		_ = webServer.Stop(stopCtx)
	}()

	// Initialize GotdAdapter
	adapter, err := gotd.NewGotdAdapter(cfg, natsBus, csm, emoState, personality, circadian, logger)
	if err != nil {
		logger.Fatal("Failed to create GotdAdapter", zap.Error(err))
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start ClockEngine
	clockEngine.Start(ctx)

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
