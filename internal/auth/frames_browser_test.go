package auth

import (
	"bytes"
	"context"
	"fmt"
	"html"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"sync/atomic"
	"testing"
	"time"

	"github.com/chromedp/chromedp"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// originGrants is the store's EmbedGrantActive for the browser test: a grant holds while its
// origin is the one allowed one.
type originGrants struct {
	Users
	allowed string
}

func (g originGrants) EmbedGrantActive(_ context.Context, s store.EmbedScope) (bool, error) {
	return s.Origin == g.allowed, nil
}

// chrome finds a Chrome or Chromium to drive: VOICEGATEWAY_TEST_CHROME, else one on PATH. Without
// one the test skips locally and fails in CI, like the container tests.
func chrome(t *testing.T) string {
	t.Helper()
	if p := os.Getenv("VOICEGATEWAY_TEST_CHROME"); p != "" {
		return p
	}
	for _, name := range []string{"google-chrome", "google-chrome-stable", "chromium", "chromium-browser"} {
		if p, err := exec.LookPath(name); err == nil {
			return p
		}
	}
	if os.Getenv("CI") != "" {
		t.Fatal("no Chrome or Chromium for the browser test; set VOICEGATEWAY_TEST_CHROME")
	}
	t.Skip("no Chrome or Chromium; set VOICEGATEWAY_TEST_CHROME to run the browser test")
	return ""
}

// In Chromium, a page on the token's origin shows the widget, and every other framing is blocked by
// the browser: another site, another project's widget, an expired token and no token (spec
// embed-tokens, check 7).
func TestBrowserFramesTheWidgetOnlyForItsOrigin(t *testing.T) {
	if testing.Short() {
		t.Skip("browser test")
	}
	exe := chrome(t)

	// Hosts are told apart by port, so each is its own origin. Each frames the widget address the
	// running case sets.
	var src atomic.Pointer[string]
	host := func(t *testing.T) *httptest.Server {
		t.Helper()
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			// The frame says it rendered by posting to its parent; a blocked frame runs no script.
			_, _ = fmt.Fprintf(w, `<!doctype html><title>host</title><script>
addEventListener("message", (e) => { if (e.data === "rendered") document.title = "rendered" })
</script><iframe src="%s" onload="setTimeout(() => { if (document.title !== 'rendered') document.title = 'blocked' }, 500)"></iframe>`, html.EscapeString(*src.Load()))
		}))
		t.Cleanup(s.Close)
		return s
	}
	allowed, other := host(t), host(t)

	now := time.Now()
	tokens, err := NewEmbedTokens(bytes.Repeat([]byte{9}, 32))
	if err != nil {
		t.Fatal(err)
	}
	tokens.Now = func() time.Time { return now }
	svc := &Service{Users: originGrants{allowed: allowed.URL}, Embeds: tokens, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}
	console := httptest.NewServer(svc.Frames(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, `<!doctype html><title>widget</title><script>parent.postMessage("rendered", "*")</script>`)
	})))
	t.Cleanup(console.Close)

	grant := store.EmbedScope{OrganisationID: "default", ProjectID: "one", Widgets: []string{"recent-calls"}, Origin: allowed.URL, KeyID: "abcdefghijkl"}
	good, _, err := tokens.Mint(grant, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	tokens.Now = func() time.Time { return now.Add(-2 * time.Hour) }
	expired, _, err := tokens.Mint(grant, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	tokens.Now = func() time.Time { return now }

	widget := func(project, token string) string {
		u := console.URL + "/org/default/project/" + project + "/embed/recent-calls"
		if token != "" {
			u += "?token=" + url.QueryEscape(token)
		}
		return u
	}

	opts := append(chromedp.DefaultExecAllocatorOptions[:], chromedp.ExecPath(exe), chromedp.NoSandbox)
	actx, cancel := chromedp.NewExecAllocator(context.Background(), opts...)
	t.Cleanup(cancel)
	bctx, cancel := chromedp.NewContext(actx)
	t.Cleanup(cancel)
	if err := chromedp.Do(bctx); err != nil {
		t.Fatalf("start the browser: %v", err)
	}

	for _, c := range []struct {
		name, host, src, want string
	}{
		{"its origin shows its project's widget", allowed.URL, widget("one", good), "rendered"},
		{"another site is blocked", other.URL, widget("one", good), "blocked"},
		{"another project's widget is blocked", allowed.URL, widget("two", good), "blocked"},
		{"an expired token is blocked", allowed.URL, widget("one", expired), "blocked"},
		{"no token is blocked", allowed.URL, widget("one", ""), "blocked"},
	} {
		t.Run(c.name, func(t *testing.T) {
			ctx, cancel := chromedp.NewContext(bctx)
			defer cancel()
			ctx, cancel = context.WithTimeout(ctx, 20*time.Second)
			defer cancel()
			src.Store(&c.src)
			err := chromedp.Do(ctx, chromedp.Navigate(c.host+"/"))
			if err == nil {
				_, err = chromedp.Run(ctx, chromedp.Poll[bool](`document.title === "rendered" || document.title === "blocked"`))
			}
			title := ""
			if err == nil {
				title, err = chromedp.Run(ctx, chromedp.Title())
			}
			if err != nil || title != c.want {
				t.Errorf("frame %s, want %s (%v)", title, c.want, err)
			}
		})
	}
}
