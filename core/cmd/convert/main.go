package main

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"

	"github.com/gotd/td/session"
)

func main() {
	authKeyHex := os.Getenv("AUTH_KEY_HEX")
	dcID := 5
	addr := "91.108.56.152:443"

	if authKeyHex == "" {
		fmt.Println("Usage: pass AUTH_KEY_HEX")
		return
	}

	authKey, err := hex.DecodeString(authKeyHex)
	if err != nil {
		fmt.Printf("Decode error: %v\n", err)
		return
	}

	h := sha1.Sum(authKey)
	authKeyID := h[len(h)-8:]

	var keyID [8]byte
	copy(keyID[:], authKeyID)

	data := &session.Data{
		DC:        dcID,
		Addr:      addr,
		AuthKey:   authKey,
		AuthKeyID: keyID[:],
	}

	storageData := struct {
		Version int           `json:"version"`
		Data    *session.Data `json:"data"`
	}{
		Version: 1,
		Data:    data,
	}

	buf, err := json.MarshalIndent(storageData, "", "  ")
	if err != nil {
		fmt.Printf("Marshal error: %v\n", err)
		return
	}

	storage := &session.FileStorage{Path: "gotd.session.json"}
	ctx := context.Background()

	if err := storage.StoreSession(ctx, buf); err != nil {
		fmt.Printf("StoreSession error: %v\n", err)
		return
	}

	fmt.Println("Successfully saved gotd.session.json!")
}
