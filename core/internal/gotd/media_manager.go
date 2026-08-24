package gotd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

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

// ResolveMediaPath resolves a filename reported in an ActionDecision
// (photo_path, sticker_id used as a local file, etc.) to an absolute path
// strictly inside the managed temp directory.
//
// These values ultimately originate from LLM output (see docs/SECURITY.md
// §2.7) and must be treated as untrusted: only the basename of the input is
// used, so directory components -- ".." traversal, absolute paths, symlink
// tricks -- are stripped entirely before the lookup. This makes it
// structurally impossible to open a file outside tempDir from an
// ActionDecision, regardless of what string a prompt-injected LLM response
// or a compromised upstream service puts in that field.
func (m *MediaManager) ResolveMediaPath(reported string) (string, error) {
	base := filepath.Base(reported)
	if reported == "" || base == "." || base == ".." || base == string(filepath.Separator) {
		return "", fmt.Errorf("invalid media filename: %q", reported)
	}

	absTempDir, err := filepath.Abs(m.tempDir)
	if err != nil {
		return "", fmt.Errorf("failed to resolve temp dir: %w", err)
	}

	candidate := filepath.Join(absTempDir, base)
	// Defense in depth: filepath.Join(absTempDir, base) already guarantees
	// this given base has no separators, but keep the check explicit in case
	// that invariant is ever weakened.
	if !strings.HasPrefix(candidate, absTempDir+string(filepath.Separator)) {
		return "", fmt.Errorf("resolved media path escapes temp dir: %q", candidate)
	}

	if _, statErr := os.Stat(candidate); statErr != nil {
		return "", fmt.Errorf("media file not found in temp dir: %w", statErr)
	}

	return candidate, nil
}
