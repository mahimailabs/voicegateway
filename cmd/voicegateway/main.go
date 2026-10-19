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
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"golang.org/x/term"

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
		userCommand(),
		keyCommand(),
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
	f.BoolVar(&in.SkipVerify, "skip-verify", false, "store the project without first checking its key and secret against LiveKit, for a server not reachable yet")

	cmd := &cobra.Command{Use: "project", Short: "Manage LiveKit projects"}
	cmd.AddCommand(add, workersCommand(), resourcesCommand(), recordingsCommand())
	return cmd
}

// workersCommand lists the Prometheus metrics endpoints of a project's self-hosted agent workers
// (phase 05d). A URL that carries a credential can come from VOICEGATEWAY_WORKER_METRICS_URL instead
// of the command line, so it stays out of shell history and process listings; either way it is
// sealed at rest and never printed back.
func workersCommand() *cobra.Command {
	var in app.WorkerEndpointCommand
	load := func() (config.Config, error) {
		c, err := config.Load(os.Getenv)
		if err != nil {
			return c, fmt.Errorf("configuration:\n%w", err)
		}
		return c, nil
	}
	add := &cobra.Command{
		Use:   "add [url]",
		Short: "List a worker's metrics URL (or from VOICEGATEWAY_WORKER_METRICS_URL)",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, err := load()
			if err != nil {
				return err
			}
			raw := os.Getenv("VOICEGATEWAY_WORKER_METRICS_URL")
			if len(args) == 1 {
				raw = args[0]
			}
			e, err := app.AddWorkerEndpoint(cmd.Context(), c, in, raw)
			if err != nil {
				return fmt.Errorf("add worker endpoint: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "added worker endpoint %s (%s) to project %s/%s\n", e.ID, e.Host, e.OrganisationID, e.ProjectID)
			return err
		},
	}
	remove := &cobra.Command{
		Use:   "remove <id>",
		Short: "Stop reading a worker metrics endpoint",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, err := load()
			if err != nil {
				return err
			}
			if err := app.RemoveWorkerEndpoint(cmd.Context(), c, in, args[0]); err != nil {
				return fmt.Errorf("remove worker endpoint: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "removed worker endpoint %s\n", args[0])
			return err
		},
	}
	list := &cobra.Command{
		Use:   "list",
		Short: "List a project's worker metrics endpoints",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, err := load()
			if err != nil {
				return err
			}
			endpoints, err := app.ListWorkerEndpoints(cmd.Context(), c, in)
			if err != nil {
				return fmt.Errorf("list worker endpoints: %w", err)
			}
			out := cmd.OutOrStdout()
			if len(endpoints) == 0 {
				_, err = fmt.Fprintln(out, "no worker endpoints listed")
				return err
			}
			for _, e := range endpoints {
				if _, err := fmt.Fprintf(out, "%s\t%s\tadded %s\n", e.ID, e.Host, e.CreatedAt.UTC().Format(time.RFC3339)); err != nil {
					return err
				}
			}
			return nil
		},
	}
	cmd := &cobra.Command{Use: "workers", Short: "Manage a project's agent worker metrics endpoints"}
	f := cmd.PersistentFlags()
	f.StringVar(&in.ProjectID, "project", "", "project id")
	f.StringVar(&in.OrganisationID, "org", "default", "organisation id")
	cmd.AddCommand(add, remove, list)
	return cmd
}

func resourcesCommand() *cobra.Command {
	var in app.WorkerEndpointCommand
	var kind, target, containerID string
	load := func() (config.Config, error) {
		c, err := config.Load(os.Getenv)
		if err != nil {
			return c, fmt.Errorf("configuration:\n%w", err)
		}
		return c, nil
	}
	add := &cobra.Command{
		Use:   "add [url]",
		Short: "List a resource's metrics URL (or from VOICEGATEWAY_RESOURCE_METRICS_URL)",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, err := load()
			if err != nil {
				return err
			}
			raw := os.Getenv("VOICEGATEWAY_RESOURCE_METRICS_URL")
			if len(args) == 1 {
				raw = args[0]
			}
			e, err := app.AddResourceEndpoint(cmd.Context(), c, in, raw, kind, target, containerID)
			if err != nil {
				return fmt.Errorf("add resource endpoint: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "added resource endpoint %s (%s) to project %s/%s\n", e.ID, e.Host, e.OrganisationID, e.ProjectID)
			return err
		},
	}
	remove := &cobra.Command{
		Use:   "remove <id>",
		Short: "Stop reading a resource metrics endpoint",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, err := load()
			if err != nil {
				return err
			}
			if err := app.RemoveResourceEndpoint(cmd.Context(), c, in, args[0]); err != nil {
				return fmt.Errorf("remove resource endpoint: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "removed resource endpoint %s\n", args[0])
			return err
		},
	}
	list := &cobra.Command{
		Use:   "list",
		Short: "List a project's resource metrics endpoints",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, err := load()
			if err != nil {
				return err
			}
			endpoints, err := app.ListResourceEndpoints(cmd.Context(), c, in)
			if err != nil {
				return fmt.Errorf("list resource endpoints: %w", err)
			}
			out := cmd.OutOrStdout()
			if len(endpoints) == 0 {
				_, err = fmt.Fprintln(out, "no resource endpoints listed")
				return err
			}
			for _, e := range endpoints {
				if _, err := fmt.Fprintf(out, "%s\t%s\t%s\t%s\t%s\tadded %s\n", e.ID, e.Kind, e.Target, e.ContainerID, e.Host, e.CreatedAt.UTC().Format(time.RFC3339)); err != nil {
					return err
				}
			}
			return nil
		},
	}
	cmd := &cobra.Command{Use: "resources", Short: "Manage a project's SFU and worker resource metrics endpoints"}
	f := cmd.PersistentFlags()
	f.StringVar(&in.ProjectID, "project", "", "project id")
	f.StringVar(&in.OrganisationID, "org", "default", "organisation id")
	add.Flags().StringVar(&kind, "kind", "", "component kind: sfu or worker")
	add.Flags().StringVar(&target, "target", "", "component identifier")
	add.Flags().StringVar(&containerID, "container", "", "exact cAdvisor container id")
	cmd.AddCommand(add, remove, list)
	return cmd
}

// recordingsCommand turns a project's recordings on or off (phase 07a, decisions 0008 and 0015).
// They are off by default; the agent must also record its session for any audio to arrive.
func recordingsCommand() *cobra.Command {
	var in app.WorkerEndpointCommand
	cmd := &cobra.Command{
		Use:       "recordings on|off",
		Short:     "Keep (on) or discard (off) the call audio a project's agents upload",
		Args:      cobra.MatchAll(cobra.ExactArgs(1), cobra.OnlyValidArgs),
		ValidArgs: []string{"on", "off"},
		RunE: func(cmd *cobra.Command, args []string) error {
			c, err := config.Load(os.Getenv)
			if err != nil {
				return fmt.Errorf("configuration:\n%w", err)
			}
			if err := app.SetRecordings(cmd.Context(), c, in, args[0] == "on"); err != nil {
				return fmt.Errorf("recordings: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "recordings %s for project %s\n", args[0], in.ProjectID)
			return err
		},
	}
	f := cmd.Flags()
	f.StringVar(&in.ProjectID, "project", "", "project id")
	f.StringVar(&in.OrganisationID, "org", "default", "organisation id")
	return cmd
}

// userCommand adds accounts and resets passwords. The first admin of an install is made here, so
// there is no open sign-up window (decision 0027). A password comes from
// VOICEGATEWAY_USER_PASSWORD or a prompt, never a flag, so it stays out of shell history and
// process listings.
func userCommand() *cobra.Command {
	var email, org, role string
	var admin bool
	add := &cobra.Command{
		Use:   "add",
		Short: "Add a user to an organisation (a new account's password from VOICEGATEWAY_USER_PASSWORD or a prompt)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if admin {
				if cmd.Flags().Changed("role") && role != "admin" {
					return errors.New("--admin and --role " + role + " disagree")
				}
				role = "admin"
			}
			if role == "" {
				return errors.New("set --role admin, member or viewer, or --admin")
			}
			c, err := config.Load(os.Getenv)
			if err != nil {
				return fmt.Errorf("configuration:\n%w", err)
			}
			created, err := app.AddUser(cmd.Context(), c, email, org, role, func() (string, error) { return readPassword(cmd) })
			if err != nil {
				return fmt.Errorf("add user: %w", err)
			}
			what := "added"
			if !created {
				what = "added the existing account"
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "%s %s to organisation %s as %s\n", what, strings.ToLower(strings.TrimSpace(email)), org, role)
			return err
		},
	}
	f := add.Flags()
	f.StringVar(&email, "email", "", "the user's email address")
	f.StringVar(&org, "org", "default", "organisation id")
	f.StringVar(&role, "role", "", "admin, member or viewer")
	f.BoolVar(&admin, "admin", false, "shorthand for --role admin")

	reset := &cobra.Command{
		Use:   "reset-password",
		Short: "Set a new password and sign the user out everywhere (password from VOICEGATEWAY_USER_PASSWORD or a prompt)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, err := config.Load(os.Getenv)
			if err != nil {
				return fmt.Errorf("configuration:\n%w", err)
			}
			if err := app.ResetPassword(cmd.Context(), c, email, func() (string, error) { return readPassword(cmd) }); err != nil {
				return fmt.Errorf("reset password: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "reset the password of %s and signed them out everywhere\n", strings.ToLower(strings.TrimSpace(email)))
			return err
		},
	}
	reset.Flags().StringVar(&email, "email", "", "the user's email address")

	cmd := &cobra.Command{Use: "user", Short: "Manage users"}
	cmd.AddCommand(add, reset)
	return cmd
}

// keyCommand creates and revokes project API keys (phase 06c), for installs without a console
// session such as CI. The full key is printed once; only its SHA-256 is stored.
func keyCommand() *cobra.Command {
	var org, project, name, keyID string
	create := &cobra.Command{
		Use:   "create",
		Short: "Create an API key for a project and print it once",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, err := config.Load(os.Getenv)
			if err != nil {
				return fmt.Errorf("configuration:\n%w", err)
			}
			full, err := app.CreateKey(cmd.Context(), c, org, project, name)
			if err != nil {
				return fmt.Errorf("create key: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "%s\nKeep this key now: it is not shown again.\n", full)
			return err
		},
	}
	create.Flags().StringVar(&org, "org", "default", "organisation id")
	create.Flags().StringVar(&project, "project", "", "project id")
	create.Flags().StringVar(&name, "name", "", "a name for the key, such as ci")
	revoke := &cobra.Command{
		Use:   "revoke",
		Short: "Revoke a project's API key by its key id",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, err := config.Load(os.Getenv)
			if err != nil {
				return fmt.Errorf("configuration:\n%w", err)
			}
			if err := app.RevokeKey(cmd.Context(), c, org, project, keyID); err != nil {
				return fmt.Errorf("revoke key: %w", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "revoked key %s\n", keyID)
			return err
		},
	}
	revoke.Flags().StringVar(&org, "org", "default", "organisation id")
	revoke.Flags().StringVar(&project, "project", "", "project id")
	revoke.Flags().StringVar(&keyID, "key-id", "", "the key's key id, the 12 characters after vgk_")
	cmd := &cobra.Command{Use: "key", Short: "Manage project API keys"}
	cmd.AddCommand(create, revoke)
	return cmd
}

// readPassword reads a password from VOICEGATEWAY_USER_PASSWORD, or else prompts for it.
func readPassword(cmd *cobra.Command) (string, error) {
	if pw := os.Getenv("VOICEGATEWAY_USER_PASSWORD"); pw != "" {
		return pw, nil
	}
	return promptPassword(cmd)
}

// promptPassword reads a password twice from the terminal without echoing it.
func promptPassword(cmd *cobra.Command) (string, error) {
	fd := int(os.Stdin.Fd()) //nolint:gosec // a file descriptor fits an int
	if !term.IsTerminal(fd) {
		return "", errors.New("set VOICEGATEWAY_USER_PASSWORD, or run in a terminal to be prompted")
	}
	read := func(prompt string) (string, error) {
		_, _ = fmt.Fprint(cmd.ErrOrStderr(), prompt)
		b, err := term.ReadPassword(fd)
		_, _ = fmt.Fprintln(cmd.ErrOrStderr())
		return string(b), err
	}
	first, err := read("Password: ")
	if err != nil {
		return "", err
	}
	again, err := read("Again: ")
	if err != nil {
		return "", err
	}
	if first != again {
		return "", errors.New("the passwords differ")
	}
	return first, nil
}
