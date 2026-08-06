package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"betteragent-core/internal/config"
)

func main() {
	cfg := config.LoadConfig()

	apiKey := cfg.GeminiAPIKey
	if apiKey == "" {
		apiKey = os.Getenv("GEMINI_API_KEY")
	}

	fmt.Printf("Loaded GeminiAPIKey length: %d\n", len(apiKey))
	fmt.Printf("Loaded Model: %s\n", cfg.YAML.LLM.Gemini.Model)

	model := cfg.YAML.LLM.Gemini.Model
	if model == "" {
		model = "gemini-3.1-flash-lite"
	}

	url := fmt.Sprintf("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s", model, apiKey)
	systemPrompt := "你是 Miao，一个惹人怜爱的猫娘 AI 助手。"
	fullPrompt := fmt.Sprintf("%s\n\n主人说: 你好呀猫娘", systemPrompt)

	reqBody, _ := json.Marshal(map[string]interface{}{
		"contents": []map[string]interface{}{
			{
				"parts": []map[string]string{
					{"text": fullPrompt},
				},
			},
		},
	})

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Post(url, "application/json", bytes.NewBuffer(reqBody))
	if err != nil {
		fmt.Printf("HTTP Request Error: %v\n", err)
		return
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	fmt.Printf("HTTP Status Code: %d\n", resp.StatusCode)
	fmt.Printf("Response Body: %s\n", string(body))
}
