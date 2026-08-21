package emotion

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"
)

type EmotionalStateStore struct {
	mu                 sync.RWMutex
	states             map[int64]*EmotionalState
	defaultPersonality *PersonalityProfile
}

func NewEmotionalStateStore(personality *PersonalityProfile) *EmotionalStateStore {
	if personality == nil {
		personality = DefaultPersonality()
	}
	return &EmotionalStateStore{
		states:             make(map[int64]*EmotionalState),
		defaultPersonality: personality,
	}
}

func (s *EmotionalStateStore) DefaultPersonality() *PersonalityProfile {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.defaultPersonality
}

func (s *EmotionalStateStore) SetDefaultPersonality(p *PersonalityProfile) {
	if p == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.defaultPersonality = p
}

func (s *EmotionalStateStore) GetOrCreate(chatID int64) *EmotionalState {
	s.mu.Lock()
	defer s.mu.Unlock()

	if st, ok := s.states[chatID]; ok {
		return st
	}

	st := NewEmotionalState()
	s.states[chatID] = st
	return st
}

func (s *EmotionalStateStore) Get(chatID int64) (*EmotionalState, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	st, ok := s.states[chatID]
	return st, ok
}

func (s *EmotionalStateStore) GetAllActiveChatIDs() []int64 {
	s.mu.RLock()
	defer s.mu.RUnlock()

	ids := make([]int64, 0, len(s.states))
	for id := range s.states {
		ids = append(ids, id)
	}
	return ids
}

func (s *EmotionalStateStore) PruneInactive(maxIdleDuration time.Duration) int {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()
	pruned := 0
	for id, st := range s.states {
		st.mu.RLock()
		lastUpdated := st.LastUpdated
		st.mu.RUnlock()

		if now.Sub(lastUpdated) > maxIdleDuration {
			delete(s.states, id)
			pruned++
		}
	}
	return pruned
}

func (s *EmotionalStateStore) Snapshot() map[int64]*EmotionalState {
	s.mu.RLock()
	defer s.mu.RUnlock()

	snapshot := make(map[int64]*EmotionalState, len(s.states))
	for id, st := range s.states {
		snapshot[id] = st.DeepCopy()
	}
	return snapshot
}

func (s *EmotionalStateStore) SaveToFileAtomic(filePath string) error {
	snapshot := s.Snapshot()

	serializable := make(map[string]*EmotionalState, len(snapshot))
	for id, st := range snapshot {
		serializable[strconv.FormatInt(id, 10)] = st
	}

	data, err := json.MarshalIndent(serializable, "", "  ")
	if err != nil {
		return fmt.Errorf("failed to marshal emotional states: %w", err)
	}

	dir := filepath.Dir(filePath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("failed to create directory %s: %w", dir, err)
	}

	tmpFile := filePath + ".tmp"
	bakFile := filePath + ".bak"

	// Write to .tmp file first
	f, err := os.OpenFile(tmpFile, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0644)
	if err != nil {
		return fmt.Errorf("failed to create tmp file %s: %w", tmpFile, err)
	}

	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpFile)
		return fmt.Errorf("failed to write to tmp file %s: %w", tmpFile, err)
	}

	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpFile)
		return fmt.Errorf("failed to sync tmp file %s: %w", tmpFile, err)
	}

	if err := f.Close(); err != nil {
		_ = os.Remove(tmpFile)
		return fmt.Errorf("failed to close tmp file %s: %w", tmpFile, err)
	}

	// Create backup copy of existing file if it exists
	if _, err := os.Stat(filePath); err == nil {
		_ = copyFile(filePath, bakFile)
	}

	// Atomic replace target file
	if err := os.Rename(tmpFile, filePath); err != nil {
		_ = os.Remove(tmpFile)
		return fmt.Errorf("failed to rename tmp file %s to %s: %w", tmpFile, filePath, err)
	}

	return nil
}

func (s *EmotionalStateStore) LoadFromFileWithRecovery(filePath string) error {
	bakFile := filePath + ".bak"

	err := s.loadFromFile(filePath)
	if err == nil {
		return nil
	}

	// Primary load failed; try loading from backup file
	if _, statErr := os.Stat(bakFile); statErr == nil {
		if bakErr := s.loadFromFile(bakFile); bakErr == nil {
			// Restore primary file from valid backup
			_ = copyFile(bakFile, filePath)
			return nil
		}
	}

	return err
}

func (s *EmotionalStateStore) loadFromFile(filePath string) error {
	f, err := os.Open(filePath)
	if err != nil {
		return err
	}
	defer f.Close()

	data, err := io.ReadAll(f)
	if err != nil {
		return fmt.Errorf("failed to read file %s: %w", filePath, err)
	}

	if len(data) == 0 {
		return fmt.Errorf("file %s is empty", filePath)
	}

	var rawMap map[string]*EmotionalState
	if err := json.Unmarshal(data, &rawMap); err != nil {
		return fmt.Errorf("failed to unmarshal JSON from %s: %w", filePath, err)
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	for k, st := range rawMap {
		chatID, err := strconv.ParseInt(k, 10, 64)
		if err != nil {
			continue
		}
		if st != nil {
			s.states[chatID] = st
		}
	}

	return nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0644)
	if err != nil {
		return err
	}
	defer out.Close()

	if _, err = io.Copy(out, in); err != nil {
		return err
	}
	return out.Sync()
}
