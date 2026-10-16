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
		role("migrate", "Apply pending database migrations", app.Migrate),
		projectCommand(),
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

// projectCommand adds LiveKit projects. The secret is read from LIVEKIT_API_SECRET, never a flag,
// so it stays out of shell history and process listings.
func projectCommand() *cobra.Command {
	var in app.ProjectInput
	add := &cobra.Command{
		Use:   "add",
		Short: "Add a LiveKit project (secret from LIVEKIT_API_SECRET)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, err := config.Load(os.Getenv)
			if err != nil {
				return fmt.Errorf("configuration:\n%w", err)
			}
			in.LiveKitAPISecret = os.Getenv("LIVEKIT_API_SECRET")
			p, err := app.AddProject(cmd.Context(), c, in)
			if err != nil {
				return fmt.Errorf("add project: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "added project %s/%s (%s) for %s, LiveKit key %s\n", p.OrganisationID, p.ID, p.Name, p.LiveKitURL, p.LiveKitAPIKey)
			return err
		},
	}
	f := add.Flags()
	f.StringVar(&in.Name, "name", "", "project name, for example \"Production\"")
	f.StringVar(&in.ID, "id", "", "URL id (default: the name as a slug)")
	f.StringVar(&in.OrganisationID, "org", "default", "organisation id")
	f.StringVar(&in.LiveKitURL, "livekit-url", os.Getenv("LIVEKIT_URL"), "LiveKit server URL (default: LIVEKIT_URL)")
	f.StringVar(&in.LiveKitAPIKey, "livekit-api-key", os.Getenv("LIVEKIT_API_KEY"), "LiveKit API key (default: LIVEKIT_API_KEY)")

	cmd := &cobra.Command{Use: "project", Short: "Manage LiveKit projects"}
	cmd.AddCommand(add)
	return cmd
}
