package modules

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSnapshotFactoryNeverDiscoversLiveWeChat(t *testing.T) {
	f, err := SnapshotFactory(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	processes, err := f.adapters.Platform.Detector().FindProcesses()
	if err != nil || len(processes) != 0 {
		t.Fatalf("live processes exposed: %v %v", processes, err)
	}
	if _, err := f.adapters.Platform.NewExtractor(4); err == nil {
		t.Fatal("live key extraction enabled")
	}
	if f.adapters.Native != nil {
		t.Fatal("native sender enabled")
	}
	if f.adapters.KeyCapture.Available() {
		t.Fatal("key capture enabled")
	}
}

func TestSnapshotFactoryRejectsOriginalDatabaseAndSymlinkEscape(t *testing.T) {
	root, outside := t.TempDir(), t.TempDir()
	f, err := SnapshotFactory(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{outside, root + "-outside", "relative"} {
		if db, err := f.adapters.OpenDB(path, ""); err == nil || db != nil {
			t.Fatalf("opened external path %q", path)
		}
	}
	link := filepath.Join(root, "linked")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	if db, err := f.adapters.OpenDB(link, ""); err == nil || db != nil {
		t.Fatal("opened symlink outside snapshot")
	}
}
