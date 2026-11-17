// snapshot-reader serves chatlog's HTTP API from a private copy of the WeChat databases.
// It never discovers or attaches to a running WeChat process and cannot extract keys.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	chatlog "github.com/sjzar/chatlog/internal/chatlog"
	"github.com/sjzar/chatlog/internal/chatlog/modules"
)

func run(root string) (err error) {
	factory, err := modules.SnapshotFactory(root)
	if err != nil {
		return err
	}
	// All configuration, runtime files and logs belong to this invocation.
	if err := os.Setenv("CHATLOG_DIR", filepath.Join(root, "config")); err != nil {
		return err
	}
	app := chatlog.NewApplicationWithFactory(factory)
	if err := app.Initialize(filepath.Join(root, "config")); err != nil {
		return err
	}
	defer func() {
		if closeErr := app.StopService(); err == nil {
			err = closeErr
		}
	}()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := app.StartService(); err != nil {
		return err
	}
	<-ctx.Done()
	return nil
}
func main() {
	root := flag.String("root", "", "absolute root of a private database snapshot")
	flag.Parse()
	if err := run(*root); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
