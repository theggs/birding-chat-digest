package modules

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/sjzar/chatlog/internal/chatlog/database"
	"github.com/sjzar/chatlog/internal/chatlog/messagehook"
	"github.com/sjzar/chatlog/internal/chatlog/ports"
	"github.com/sjzar/chatlog/internal/wechat/backend"
	"github.com/sjzar/chatlog/internal/wechat/key"
	"github.com/sjzar/chatlog/internal/wechat/model"
	"github.com/sjzar/chatlog/internal/wechat/process"
	"github.com/sjzar/chatlog/internal/wechatdb"
)

// SnapshotFactory has no live process discovery, native sender or key capture.
// Its database opener cannot leave root, even via a symlink or control action.
func SnapshotFactory(root string) (*Factory, error) {
	if !filepath.IsAbs(root) {
		return nil, fmt.Errorf("snapshot root must be absolute")
	}
	root, err := filepath.EvalSymlinks(root)
	if err != nil {
		return nil, err
	}
	f := PlatformFactory()
	f.adapters.Platform = snapshotBackend{Backend: f.adapters.Platform}
	f.adapters.Native = nil
	f.adapters.KeyCapture = snapshotCapture{}
	f.adapters.OpenHook = func(c database.Config, db database.HookDatabase) (database.HookRuntime, error) {
		return messagehook.NewWithNative(c, db, nil)
	}
	openDB := f.adapters.OpenDB
	f.adapters.OpenDB = func(path, key string) (*wechatdb.DB, error) {
		if !filepath.IsAbs(path) {
			return nil, fmt.Errorf("database must be inside snapshot")
		}
		resolved, err := filepath.EvalSymlinks(path)
		if err != nil {
			return nil, err
		}
		rel, err := filepath.Rel(root, resolved)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return nil, fmt.Errorf("database must be inside snapshot")
		}
		return openDB(resolved, key)
	}
	return f, nil
}

type snapshotBackend struct{ backend.Backend }

func (snapshotBackend) Detector() process.Detector { return snapshotDetector{} }
func (snapshotBackend) NewExtractor(int) (key.Extractor, error) {
	return nil, fmt.Errorf("snapshot reader cannot extract keys")
}

type snapshotDetector struct{}

func (snapshotDetector) FindProcesses() ([]*model.Process, error) { return nil, nil }

type snapshotCapture struct{}

func (snapshotCapture) Available() bool                   { return false }
func (snapshotCapture) IsImagePermissionError(error) bool { return false }
func (snapshotCapture) ExtractImageKey(context.Context, uint32, string, func(string)) (string, error) {
	return "", fmt.Errorf("snapshot reader cannot extract keys")
}
func (snapshotCapture) CaptureDataKeys(context.Context, ports.KeyCaptureTarget, func(string)) (ports.DataKeyCapture, error) {
	return ports.DataKeyCapture{}, fmt.Errorf("snapshot reader cannot extract keys")
}
func (snapshotCapture) ApplyDataKeys(string, ports.DataKeyCapture, func(string)) error {
	return fmt.Errorf("snapshot reader cannot apply keys")
}
