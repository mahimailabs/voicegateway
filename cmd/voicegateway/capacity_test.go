package main

import (
	"bytes"
	"context"
	"os"
	"strings"
	"testing"
	"testing/synctest"
)

// TestWatchSignals pins the Ctrl-C contract: one signal stops cleanly, a second gives up with
// a warning and status 130, and the command waits for a give-up already under way.
func TestWatchSignals(t *testing.T) {
	tests := []struct {
		name     string
		signals  int
		holdExit bool // exit blocks until the run has returned, to catch the race
		wantStop bool
		wantExit int // 0: exit never called
	}{
		{name: "no signal ends with the run", signals: 0},
		{name: "one signal stops cleanly", signals: 1, wantStop: true},
		{name: "a second signal gives up with 130", signals: 2, wantStop: true, wantExit: 130},
		{name: "the run returning waits for a give-up under way", signals: 2, holdExit: true, wantStop: true, wantExit: 130},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				ctx, cancel := context.WithCancel(t.Context())
				sig := make(chan os.Signal, 2)
				stop := make(chan struct{})
				var stderr bytes.Buffer
				gate := make(chan struct{})
				code := 0
				exit := func(c int) {
					code = c
					if tt.holdExit {
						<-gate
					}
				}
				done := watchSignals(ctx, sig, stop, &stderr, exit)
				for range tt.signals {
					sig <- os.Interrupt
				}
				synctest.Wait()

				// The run returns: the command cancels and waits, as RunE does.
				cancel()
				synctest.Wait()
				if tt.holdExit {
					select {
					case <-done:
						t.Fatal("watcher finished while exit was still running")
					default:
					}
					close(gate)
					synctest.Wait()
				}
				select {
				case <-done:
				default:
					t.Fatal("watcher did not finish")
				}

				select {
				case <-stop:
					if !tt.wantStop {
						t.Error("stop closed without a signal")
					}
				default:
					if tt.wantStop {
						t.Error("stop not closed after a signal")
					}
				}
				if code != tt.wantExit {
					t.Errorf("exit code %d, want %d", code, tt.wantExit)
				}
				warned := strings.Contains(stderr.String(), "gave up without hanging up")
				if warned != (tt.wantExit != 0) {
					t.Errorf("warning printed %v, want %v", warned, tt.wantExit != 0)
				}
			})
		})
	}
}
