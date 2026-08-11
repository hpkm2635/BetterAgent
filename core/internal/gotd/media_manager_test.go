package gotd

import (
	"os"
	"path/filepath"
	"testing"

	"go.uber.org/zap"
)

func TestResolveMediaPath_AllowsPlainFilenameInsideTempDir(t *testing.T) {
	dir := t.TempDir()
	mm, err := NewMediaManager(dir, zap.NewNop())
	if err != nil {
		t.Fatalf("NewMediaManager failed: %v", err)
	}

	realFile := filepath.Join(dir, "photo_123.jpg")
	if err := os.WriteFile(realFile, []byte("x"), 0644); err != nil {
		t.Fatalf("failed to seed temp file: %v", err)
	}

	resolved, err := mm.ResolveMediaPath("photo_123.jpg")
	if err != nil {
		t.Fatalf("expected safe filename to resolve, got error: %v", err)
	}
	absDir, _ := filepath.Abs(dir)
	if resolved != filepath.Join(absDir, "photo_123.jpg") {
		t.Errorf("resolved path mismatch: got %q", resolved)
	}
}

func TestResolveMediaPath_RejectsPathTraversal(t *testing.T) {
	dir := t.TempDir()
	mm, err := NewMediaManager(dir, zap.NewNop())
	if err != nil {
		t.Fatalf("NewMediaManager failed: %v", err)
	}

	// Plant a sensitive file just outside the managed temp dir to prove it's unreachable.
	parentDir := filepath.Dir(dir)
	secretPath := filepath.Join(parentDir, "gotd.session.json")
	if err := os.WriteFile(secretPath, []byte("super-secret"), 0644); err != nil {
		t.Fatalf("failed to seed secret file: %v", err)
	}
	defer os.Remove(secretPath)

	maliciousInputs := []string{
		"../gotd.session.json",
		"../../gotd.session.json",
		"../../../etc/passwd",
		secretPath, // absolute path pointing straight at the secret
		"",
		".",
		"..",
	}

	for _, in := range maliciousInputs {
		if _, err := mm.ResolveMediaPath(in); err == nil {
			t.Errorf("expected ResolveMediaPath(%q) to be rejected, but it succeeded", in)
		}
	}
}

func TestResolveMediaPath_RejectsNonexistentFile(t *testing.T) {
	dir := t.TempDir()
	mm, err := NewMediaManager(dir, zap.NewNop())
	if err != nil {
		t.Fatalf("NewMediaManager failed: %v", err)
	}

	if _, err := mm.ResolveMediaPath("does_not_exist.jpg"); err == nil {
		t.Errorf("expected error for nonexistent file, got nil")
	}
}
