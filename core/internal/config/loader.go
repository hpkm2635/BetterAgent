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
		ClockTickSeconds    int `yaml:"clock_tick_seconds"`
		TypingDelaySeconds  int `yaml:"typing_delay_seconds"`
		AntiSpamMaxRate     int `yaml:"anti_spam_max_rate"`
	} `yaml:"core_engine"`
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
	YAML                     YAMLConfig
}

func LoadConfig() *Config {
	_ = godotenv.Overload("../.env") // load root .env
	_ = godotenv.Load(".env")        // load local .env if any

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
		YAML:                     yc,
	}
}

func getEnv(key, fallback string) string {
	if val, ok := os.LookupEnv(key); ok && val != "" {
		return val
	}
	return fallback
}
