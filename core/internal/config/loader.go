package config

import (
	"log"
	"os"
	"strconv"
	"strings"

	"github.com/joho/godotenv"
	"gopkg.in/yaml.v3"
)

type YAMLConfig struct {
	App struct {
		Name        string `yaml:"name"`
		Version     string `yaml:"version"`
		Environment string `yaml:"environment"`
	} `yaml:"app"`
	Network struct {
		HTTPProxy  string `yaml:"http_proxy"`
		HTTPSProxy string `yaml:"https_proxy"`
	} `yaml:"network"`
	Infrastructure struct {
		NatsURL   string `yaml:"nats_url"`
		RedisURL  string `yaml:"redis_url"`
		QdrantURL string `yaml:"qdrant_url"`
	} `yaml:"infrastructure"`
	LLM struct {
		DefaultProvider string `yaml:"default_provider"`
		Gemini          struct {
			Model string `yaml:"model"`
		} `yaml:"gemini"`
		Claude struct {
			Model string `yaml:"model"`
		} `yaml:"claude"`
	} `yaml:"llm"`
	Memory struct {
		ShortTermMaxMessages int     `yaml:"short_term_max_messages"`
		QdrantCollection     string  `yaml:"qdrant_collection"`
		EbbinghausLambda     float64 `yaml:"ebbinghaus_lambda"`
		TopKRAG              int     `yaml:"top_k_rag"`
	} `yaml:"memory"`
	CoreEngine struct {
		ClockTickSeconds   int    `yaml:"clock_tick_seconds"`
		TypingDelaySeconds int    `yaml:"typing_delay_seconds"`
		AntiSpamMaxRate    int    `yaml:"anti_spam_max_rate"`
		GameEventBindAddr  string `yaml:"game_event_bind_addr"`
		Urge               struct {
			AlphaBoredom                float64 `yaml:"alpha_boredom"`
			BetaGameEvent               float64 `yaml:"beta_game_event"`
			GammaUnreadPressure         float64 `yaml:"gamma_unread_pressure"`
			BaseThreshold               float64 `yaml:"base_threshold"`
			ArousalSensitivity          float64 `yaml:"arousal_sensitivity"`
			EnergyPenalty               float64 `yaml:"energy_penalty"`
			MinThreshold                float64 `yaml:"min_threshold"`
			MaxThreshold                float64 `yaml:"max_threshold"`
			UrgeCap                     float64 `yaml:"urge_cap"`
			CooldownSeconds             int     `yaml:"cooldown_seconds"`
			DeadZoneSeconds             int     `yaml:"dead_zone_seconds"`
			GameEventDecaySeconds       int     `yaml:"game_event_decay_seconds"`
			UnreadPressureWindowSeconds int     `yaml:"unread_pressure_window_seconds"`
			PrimaryChatID               int64   `yaml:"primary_chat_id"`
			TargetSessionMaxAgeSeconds  int     `yaml:"target_session_max_age_seconds"`
		} `yaml:"urge"`
	} `yaml:"core_engine"`
	GameEvents struct {
		DefaultWeight float64                       `yaml:"default_weight"`
		Games         map[string]map[string]float64 `yaml:"games"`
	} `yaml:"game_events"`
}

type Config struct {
	TelegramAPIID   int
	TelegramAPIHash string
	TelegramPhone   string
	GeminiAPIKey    string
	NatsURL         string
	NatsUser        string
	NatsPassword    string
	RedisURL        string
	QdrantURL       string
	WebGatewayToken string
	// WebGatewayAllowedOrigins, if set, restricts which browser Origins may
	// open the /ws WebSocket (glob patterns, e.g. "example.com", "*.example.com").
	// If empty, Origin is not checked (WEBGATEWAY_TOKEN is still required either way).
	WebGatewayAllowedOrigins []string
	// GameEventToken gates POST /api/game-event (see webgateway/game_event_handler.go).
	// Empty disables the endpoint rather than failing startup -- it's an
	// optional integration, unlike WEBGATEWAY_TOKEN.
	GameEventToken string
	YAML           YAMLConfig
}

func LoadConfig() *Config {
	// Load the CWD-local .env first (non-overriding Load, so whatever it
	// sets wins for the rest of this call) so it's the one that determines
	// secrets like WEBGATEWAY_TOKEN. Only THEN fall back to "../.env" to
	// fill in anything the local file didn't set.
	//
	// This order matters because "../.env" isn't just a fallback in every
	// layout: in dev mode the Go binary's CWD is core/, so "../.env" is the
	// real repo-root .env and there's usually no core/.env to compete with
	// it -- fine either way. But the portable "绿化包" package runs with
	// CWD at the package root (its own .env lives right there), and that
	// root happens to sit *inside* the dev repo checkout -- so "../.env"
	// silently resolves to the DEVELOPER's repo-root .env instead of being
	// a harmless miss. Loading that first with Overload (as this used to)
	// force-overwrote the portable package's own pinned secrets (e.g.
	// WEBGATEWAY_TOKEN, pinned to match what the frontend bundle was
	// compiled against) with the developer's unrelated dev-mode values,
	// with no way for the correct local .env to win afterwards since Load
	// doesn't override what's already set. Local-first with plain Load
	// (never Overload) means the package's own .env is always the source
	// of truth for anything it defines, in both layouts.
	_ = godotenv.Load(".env")
	_ = godotenv.Load("../.env")

	apiIDStr := getEnv("TELEGRAM_API_ID", "0")
	apiID, err := strconv.Atoi(apiIDStr)
	if err != nil {
		log.Printf("Warning: Invalid TELEGRAM_API_ID: %v", err)
	}

	var yc YAMLConfig

	// Search for config.yaml or config.yaml.example
	candidates := []string{
		"../config/config.yaml",
		"../config/config.yaml.example",
		"config/config.yaml",
		"config/config.yaml.example",
	}

	for _, path := range candidates {
		if _, err := os.Stat(path); err == nil {
			data, err := os.ReadFile(path)
			if err == nil {
				if err := yaml.Unmarshal(data, &yc); err == nil {
					log.Printf("Successfully loaded config from %s", path)
					break
				}
			}
		}
	}

	// Environment variable overrides for infrastructure.
	// Defaults use 127.0.0.1 explicitly, not "localhost" -- see docs/SECURITY.md §2.8.
	natsURL := getEnv("NATS_URL", yc.Infrastructure.NatsURL)
	if natsURL == "" {
		natsURL = "nats://127.0.0.1:4222"
	}
	redisURL := getEnv("REDIS_URL", yc.Infrastructure.RedisURL)
	if redisURL == "" {
		redisURL = "redis://127.0.0.1:6379"
	}
	qdrantURL := getEnv("QDRANT_URL", yc.Infrastructure.QdrantURL)
	if qdrantURL == "" {
		qdrantURL = "http://127.0.0.1:6333"
	}

	var allowedOrigins []string
	for _, o := range strings.Split(getEnv("WEBGATEWAY_ALLOWED_ORIGINS", ""), ",") {
		if trimmed := strings.TrimSpace(o); trimmed != "" {
			allowedOrigins = append(allowedOrigins, trimmed)
		}
	}

	return &Config{
		TelegramAPIID:            apiID,
		TelegramAPIHash:          getEnv("TELEGRAM_API_HASH", ""),
		TelegramPhone:            getEnv("TELEGRAM_PHONE", ""),
		GeminiAPIKey:             getEnv("GEMINI_API_KEY", ""),
		NatsURL:                  natsURL,
		NatsUser:                 getEnv("NATS_USER", ""),
		NatsPassword:             getEnv("NATS_PASSWORD", ""),
		RedisURL:                 redisURL,
		QdrantURL:                qdrantURL,
		WebGatewayToken:          getEnv("WEBGATEWAY_TOKEN", ""),
		WebGatewayAllowedOrigins: allowedOrigins,
		GameEventToken:           getEnv("GAME_EVENT_TOKEN", ""),
		YAML:                     yc,
	}
}

func getEnv(key, fallback string) string {
	if val, ok := os.LookupEnv(key); ok && val != "" {
		return val
	}
	return fallback
}
