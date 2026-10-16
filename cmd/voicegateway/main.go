// Command voicegateway is the one VoiceGateway binary. It runs as web or worker (decision 0006),
// and preflight checks every dependency before either starts.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/spf13/cobra"

	"github.com/mahimailabs/voicegateway/internal/app"
	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/obs"
	"github.com/mahimailabs/voicegateway/internal/preflight"
)

// errChecksFailed is returned when preflight ran but a dependency did not answer.
var errChecksFailed = errors.New("preflight: one or more dependencies did not answer")

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	err := root().ExecuteContext(ctx)
	stop()
	if err != nil {
		if !errors.Is(err, errChecksFailed) {
			fmt.Fprintln(os.Stderr, err)
		}
		os.Exit(1)
	}
}

func root() *cobra.Command {
	cmd := &cobra.Command{
		Use:               "voicegateway",
		Short:             "Profile LiveKit voice agents end to end",
		SilenceUsage:      true,
		SilenceErrors:     true,
		CompletionOptions: cobra.CompletionOptions{DisableDefaultCmd: true},
	}
	role := func(name, short string, run func(context.Context, config.Config, *slog.Logger) error) *cobra.Command {
		return &cobra.Command{
			Use:   name,
			Short: short,
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				c, err := config.Load(os.Getenv)
				if err != nil {
					return fmt.Errorf("configuration:\n%w", err)
				}
				return run(cmd.Context(), c, obs.Logger(os.Stdout, c.LogLevel, name))
			},
		}
	}
	cmd.AddCommand(
		role("web", "Take data in and serve the API", app.Web),
		role("worker", "Turn data into calls and store it", app.Worker),
		&cobra.Command{
			Use:   "preflight",
			Short: "Check that every dependency answers",
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				c, err := config.Load(os.Getenv)
				if err != nil {
					return fmt.Errorf("configuration:\n%w", err)
				}
				results := preflight.Preflight{Checks: app.Checks(c), Timeout: 5 * time.Second}.Run(cmd.Context())
				ok, err := preflight.Report(cmd.OutOrStdout(), results)
				if err != nil {
					return err
				}
				if !ok {
					return errChecksFailed
				}
				return nil
			},
		},
	)
	return cmd
}
