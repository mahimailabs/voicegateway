package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"slices"
	"strings"
	"syscall"

	"github.com/spf13/cobra"
	"golang.org/x/term"

	"github.com/mahimailabs/voicegateway/internal/app"
	"github.com/mahimailabs/voicegateway/internal/capacity"
)

// version is the release this binary was built from, set by the image build (-X main.version).
var version = "dev"

// errNoConsent is returned when a person declines, or cannot be asked, to place real calls.
var errNoConsent = errors.New("no test calls placed: run again with --yes to place them")

// capacityCommand runs one capacity test from the command line, with no database, console or
// install (docs/specs/capacity-command.md). The key and secret come from LIVEKIT_API_KEY and
// LIVEKIT_API_SECRET, never flags, so they stay out of shell history and process listings.
func capacityCommand() *cobra.Command {
	in := app.CapacityInput{Refine: true}
	var yes, noRefine bool
	cmd := &cobra.Command{
		Use:   "capacity",
		Short: "Find how many calls at once your LiveKit agent carries, and write a report",
		Long: `Places synthetic calls at your agent, 1, 2, 4 and so on up to --max at once, 60 s per step,
until the agent refuses, drops or slows down. Then it tests the numbers in between, and writes
capacity-report.html, capacity-report.md and capacity-report.json.

The key and secret come from LIVEKIT_API_KEY and LIVEKIT_API_SECRET. Test calls go through your
agent's own speech, model and voice providers, which bill you for them. The agent must be idle:
the test stops if a real call reaches it. Ctrl-C stops the test and deletes every test room.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			in.APIKey, in.APISecret = os.Getenv("LIVEKIT_API_KEY"), os.Getenv("LIVEKIT_API_SECRET")
			in.Refine, in.Version = !noRefine, version
			if err := checkCapacityInput(in); err != nil {
				return err
			}
			out := cmd.OutOrStdout()
			steps := capacity.Ladder(in.MaxStep)
			minutes := len(steps) + 2
			_, _ = fmt.Fprintf(out, "Capacity test of agent %q on %s\n", in.Agent, in.LiveKitURL)
			_, _ = fmt.Fprintf(out, "Up to %d calls at once (steps %s), 60 s each, about %d minutes.\n", in.MaxStep, joinInts(steps), minutes)
			_, _ = fmt.Fprintf(out, "Every call is real: it runs your agent's speech, model and voice providers, which bill you.\n")
			if !yes {
				ok, err := confirm(cmd)
				if err != nil {
					return err
				}
				if !ok {
					return errNoConsent
				}
			}

			// The first Ctrl-C stops the test cleanly; a second one gives up at once.
			ctx, cancel := context.WithCancel(context.WithoutCancel(cmd.Context()))
			defer cancel()
			sig := make(chan os.Signal, 2)
			signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
			defer signal.Stop(sig)
			stop := make(chan struct{})
			go func() {
				select {
				case <-sig:
				case <-ctx.Done():
					return
				}
				close(stop)
				select {
				case <-sig:
					cancel()
				case <-ctx.Done():
				}
			}()

			rep, err := app.RunCapacity(ctx, in, stop, out)
			if err != nil {
				return err
			}
			_, _ = fmt.Fprintf(out, "\n%s\n", rep.Verdict)
			for _, l := range append(rep.Limit, rep.Next...) {
				_, _ = fmt.Fprintf(out, "  %s\n", l)
			}
			_, _ = fmt.Fprintf(out, "\n%s\nReport: %s\n", rep.Table(), strings.Join(prefix(in.OutDir, app.CapacityFiles), ", "))
			return nil
		},
	}
	f := cmd.Flags()
	f.StringVar(&in.LiveKitURL, "livekit-url", os.Getenv("LIVEKIT_URL"), "LiveKit server URL (default: LIVEKIT_URL)")
	f.StringVar(&in.Agent, "agent", "", "the agent's name, as registered with agent_name (required)")
	f.IntVar(&in.MaxStep, "max", 16, "most calls at once: 8, 16, 32 or 64")
	f.StringVar(&in.OutDir, "out", ".", "folder for the report files")
	f.Float64Var(&in.JoinMs, "join-threshold-ms", 2000, "a join slower than this counts as late")
	f.StringVar(&in.AgentServer, "agent-server", "", `the agent's server size for the report, such as "4 vCPU, 8 GB"`)
	f.IntVar(&in.AgentServers, "agent-servers", 0, "how many servers run the agent, to work out calls per server")
	f.IntVar(&in.TargetCalls, "target-calls", 0, "calls at once you need; the report works out the servers it takes")
	f.BoolVar(&yes, "yes", false, "place the test calls without asking")
	f.BoolVar(&noRefine, "no-refine", false, "stop at the first failed step instead of testing the numbers in between")
	return cmd
}

func checkCapacityInput(in app.CapacityInput) error {
	var missing []string
	if in.LiveKitURL == "" {
		missing = append(missing, "--livekit-url (or LIVEKIT_URL)")
	}
	if in.Agent == "" {
		missing = append(missing, "--agent")
	}
	if in.APIKey == "" {
		missing = append(missing, "LIVEKIT_API_KEY")
	}
	if in.APISecret == "" {
		missing = append(missing, "LIVEKIT_API_SECRET")
	}
	if len(missing) > 0 {
		return fmt.Errorf("capacity: missing %s", strings.Join(missing, ", "))
	}
	if !slices.Contains([]int{8, 16, 32, 64}, in.MaxStep) {
		return fmt.Errorf("capacity: --max must be 8, 16, 32 or 64, not %d", in.MaxStep)
	}
	if in.AgentServers < 0 || in.TargetCalls < 0 {
		return errors.New("capacity: --agent-servers and --target-calls cannot be negative")
	}
	if in.JoinMs < 100 || in.JoinMs > 60000 {
		return fmt.Errorf("capacity: --join-threshold-ms must be between 100 and 60000")
	}
	return nil
}

// confirm asks on a terminal; with no terminal to ask on, it refuses rather than place calls.
func confirm(cmd *cobra.Command) (bool, error) {
	if !term.IsTerminal(int(os.Stdin.Fd())) { //nolint:gosec // a file descriptor fits an int
		return false, errNoConsent
	}
	_, _ = fmt.Fprint(cmd.OutOrStdout(), "Place the test calls? [y/N] ")
	line, err := bufio.NewReader(cmd.InOrStdin()).ReadString('\n')
	if err != nil && line == "" {
		return false, nil //nolint:nilerr // no answer is a no
	}
	a := strings.ToLower(strings.TrimSpace(line))
	return a == "y" || a == "yes", nil
}

func joinInts(xs []int) string {
	s := make([]string, len(xs))
	for i, x := range xs {
		s[i] = fmt.Sprint(x)
	}
	return strings.Join(s, ", ")
}

func prefix(dir string, names []string) []string {
	out := make([]string, len(names))
	for i, n := range names {
		if dir == "." {
			out[i] = n
		} else {
			out[i] = strings.TrimSuffix(dir, "/") + "/" + n
		}
	}
	return out
}
