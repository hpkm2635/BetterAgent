package emotion

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestEmotionalStateStore_GetOrCreateAndDeepCopy(t *testing.T) {
	personality := DefaultPersonality()
	store := NewEmotionalStateStore(personality)

	st1 := store.GetOrCreate(1001)
	if st1 == nil {
		t.Fatal("expected non-nil EmotionalState for chat 1001")
	}

	st1.ApplySentimentDelta(0.2, 0.1, 10.0)
	if st1.AffectionLevel != 60.0 {
		t.Fatalf("expected AffectionLevel 60.0, got %.1f", st1.AffectionLevel)
	}

	snapshot := store.Snapshot()
	snapSt, ok := snapshot[1001]
	if !ok {
		t.Fatal("expected chat 1001 in snapshot")
	}

	// Modify original, check that snapshot is isolated
	st1.ApplySentimentDelta(0.1, 0.0, 5.0)
	if snapSt.AffectionLevel != 60.0 {
		t.Fatalf("expected snapshot AffectionLevel 60.0, got %.1f", snapSt.AffectionLevel)
	}
	if st1.AffectionLevel != 65.0 {
		t.Fatalf("expected original AffectionLevel 65.0, got %.1f", st1.AffectionLevel)
	}
}

func TestEmotionalStateStore_JealousyAndSatietyDecay(t *testing.T) {
	st := NewEmotionalState()
	st.SetJealousy(0.8)
	if !st.IsJealous() {
		t.Fatal("expected IsJealous() to be true")
	}
	if st.CurrentMoodTag != MoodJealous {
		t.Fatalf("expected MoodJealous, got %s", st.CurrentMoodTag)
	}

	// Decay over 6 hours
	st.ApplyTimeDecay(6*time.Hour, 1.0)
	if st.IsJealous() {
		t.Fatal("expected IsJealous() to decay to false after 6 hours")
	}

	// Test Satiety hunger mood shift
	st.ApplySatietyDelta(-1.0) // Empty satiety
	if st.CurrentMoodTag != MoodMoody {
		t.Fatalf("expected MoodMoody when Satiety < 0.2, got %s", st.CurrentMoodTag)
	}
}

func TestEmotionalStateStore_AtomicSaveAndRecovery(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "emotion_test_*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tmpDir)

	filePath := filepath.Join(tmpDir, "emotion_states.json")
	bakPath := filePath + ".bak"

	store := NewEmotionalStateStore(DefaultPersonality())
	st := store.GetOrCreate(2002)
	st.ApplySentimentDelta(0.4, 0.2, 25.0)
	st.SetJealousy(0.9)

	// Save atomically
	if err := store.SaveToFileAtomic(filePath); err != nil {
		t.Fatalf("SaveToFileAtomic failed: %v", err)
	}

	// Verify file exists
	if _, err := os.Stat(filePath); err != nil {
		t.Fatalf("expected json file to exist: %v", err)
	}

	// Save again to produce .bak file
	st.ApplySentimentDelta(-0.1, 0.0, 5.0)
	if err := store.SaveToFileAtomic(filePath); err != nil {
		t.Fatalf("second SaveToFileAtomic failed: %v", err)
	}
	if _, err := os.Stat(bakPath); err != nil {
		t.Fatalf("expected bak file to exist: %v", err)
	}

	// Corrupt main file to 0 bytes
	if err := os.WriteFile(filePath, []byte(""), 0644); err != nil {
		t.Fatalf("failed to corrupt main file: %v", err)
	}

	// Load with recovery from .bak
	newStore := NewEmotionalStateStore(DefaultPersonality())
	if err := newStore.LoadFromFileWithRecovery(filePath); err != nil {
		t.Fatalf("LoadFromFileWithRecovery failed to recover from .bak: %v", err)
	}

	recoveredSt, ok := newStore.Get(2002)
	if !ok {
		t.Fatal("expected chat 2002 to be recovered from .bak file")
	}
	if recoveredSt.AffectionLevel < 70.0 {
		t.Fatalf("expected recovered AffectionLevel around 75.0 or 80.0, got %.1f", recoveredSt.AffectionLevel)
	}
}

func TestEmotionalStateStore_PruneInactive(t *testing.T) {
	store := NewEmotionalStateStore(DefaultPersonality())
	st1 := store.GetOrCreate(3001)
	st2 := store.GetOrCreate(3002)

	// Set st1 LastUpdated to 2 hours ago
	st1.mu.Lock()
	st1.LastUpdated = time.Now().Add(-2 * time.Hour)
	st1.mu.Unlock()

	// Keep st2 fresh
	st2.mu.Lock()
	st2.LastUpdated = time.Now()
	st2.mu.Unlock()

	pruned := store.PruneInactive(1 * time.Hour)
	if pruned != 1 {
		t.Fatalf("expected 1 pruned chat, got %d", pruned)
	}

	if _, ok := store.Get(3001); ok {
		t.Fatal("expected chat 3001 to be pruned")
	}
	if _, ok := store.Get(3002); !ok {
		t.Fatal("expected chat 3002 to remain in store")
	}
}
