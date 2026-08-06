package gotd

import (
	"fmt"
	"os"
	"path/filepath"

	"go.uber.org/zap"
)

type MediaManager struct {
	tempDir string
	logger  *zap.Logger
}

func NewMediaManager(tempDir string, logger *zap.Logger) (*MediaManager, error) {
	if err := os.MkdirAll(tempDir, 0755); err != nil {
		return nil, fmt.Errorf("failed to create temp dir %s: %w", tempDir, err)
	}
	return &MediaManager{
		tempDir: tempDir,
		logger:  logger,
	}, nil
}

func (m *MediaManager) GetTempDir() string {
	return m.tempDir
}

func (m *MediaManager) CleanOldFiles(maxAgeSec int64) {
	entries, err := os.ReadDir(m.tempDir)
	if err != nil {
		return
	}

	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			continue
		}
		if info.ModTime().Unix() < maxAgeSec {
			_ = os.Remove(filepath.Join(m.tempDir, entry.Name()))
		}
	}
}
