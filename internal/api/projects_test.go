package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestSlugMatchesTheConsole(t *testing.T) {
	tests := map[string]string{
		"Default project":              "default-project",
		"  Production  ":               "production",
		"EU / Staging #2":              "eu-staging-2",
		strings.Repeat("a", 70):        strings.Repeat("a", 63),
		strings.Repeat("a", 62) + " b": strings.Repeat("a", 62),
	}
	for in, want := range tests {
		if got := Slug(in); got != want {
			t.Errorf("Slug(%q) = %q, want %q", in, got, want)
		}
	}
}

// CheckProject is the one rule createProject and voicegateway project add share (phase 06f).
func TestCheckProject(t *testing.T) {
	ok := store.NewProject{OrganisationID: "org-a", Name: "  Production  ", LiveKitURL: " wss://lk.example.com ", LiveKitAPIKey: " APIkey ", LiveKitAPISecret: " kept as given "}
	got, err := CheckProject(ok)
	if err != nil {
		t.Fatal(err)
	}
	if want := (store.NewProject{OrganisationID: "org-a", ID: "production", Name: "Production", LiveKitURL: "wss://lk.example.com", LiveKitAPIKey: "APIkey", LiveKitAPISecret: " kept as given "}); got != want {
		t.Errorf("CheckProject = %+v, want %+v", got, want)
	}
	for _, scheme := range []string{"ws", "wss", "http", "https"} {
		p := ok
		p.LiveKitURL = scheme + "://lk.example.com:7880"
		if _, err := CheckProject(p); err != nil {
			t.Errorf("%s URL refused: %v", scheme, err)
		}
	}
	with := func(change func(*store.NewProject)) store.NewProject {
		p := ok
		change(&p)
		return p
	}
	for _, tt := range []struct {
		name string
		in   store.NewProject
		want string
	}{
		{"every field missing", store.NewProject{LiveKitAPISecret: "   "}, "missing name, livekitUrl, livekitApiKey, livekitApiSecret"},
		{"no letter or digit", with(func(p *store.NewProject) { p.Name = "!!!" }), "name must be"},
		{"name too long", with(func(p *store.NewProject) { p.Name = strings.Repeat("x", 65) }), "name must be"},
		{"control character", with(func(p *store.NewProject) { p.Name = "a\x00b" }), "name must be"},
		{"bad id", with(func(p *store.NewProject) { p.ID = "-x" }), "id must be"},
		{"other scheme", with(func(p *store.NewProject) { p.LiveKitURL = "ftp://lk.example.com" }), "livekitUrl must be"},
		{"no host", with(func(p *store.NewProject) { p.LiveKitURL = "wss://" }), "livekitUrl must be"},
		{"not a URL", with(func(p *store.NewProject) { p.LiveKitURL = "lk.example.com" }), "livekitUrl must be"},
		{"user information", with(func(p *store.NewProject) { p.LiveKitURL = "wss://u:p@lk.example.com" }), "livekitUrl must be"},
		{"URL too long", with(func(p *store.NewProject) { p.LiveKitURL = "wss://lk.example.com/" + strings.Repeat("x", 2048) }), "livekitUrl must be"},
		{"space in key", with(func(p *store.NewProject) { p.LiveKitAPIKey = "API key" }), "livekitApiKey must be"},
		{"key too long", with(func(p *store.NewProject) { p.LiveKitAPIKey = strings.Repeat("k", 257) }), "livekitApiKey must be"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			_, err := CheckProject(tt.in)
			var invalid *ProjectInputError
			if !errors.As(err, &invalid) || !strings.HasPrefix(err.Error(), tt.want) {
				t.Errorf("CheckProject = %v, want a ProjectInputError starting %q", err, tt.want)
			}
		})
	}
}

// The body limit applies to the whole request, including bytes after a valid object.
func TestCreateProjectRejectsTrailingBody(t *testing.T) {
	body := `{"name":"Production","livekitUrl":"wss://lk.example.com","livekitApiKey":"APIkey","livekitApiSecret":"test-secret"}`
	for name, suffix := range map[string]string{"second object": "{}", "garbage": "x", "oversize whitespace": strings.Repeat(" ", maxProjectBody)} {
		t.Run(name, func(t *testing.T) {
			creator := &countProjectCreates{}
			a := &API{NewProjects: creator, VerifyLiveKit: verifyAs{}}
			w := httptest.NewRecorder()
			a.createProject(w, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(body+suffix)))
			if w.Code != http.StatusBadRequest || creator.calls != 0 {
				t.Fatalf("status=%d creates=%d, want 400 and no write", w.Code, creator.calls)
			}
		})
	}
}

type countProjectCreates struct {
	calls int
	last  store.NewProject
}

func (c *countProjectCreates) CreateProject(_ context.Context, p store.NewProject) (store.Project, error) {
	c.calls++
	c.last = p
	return store.Project{ID: p.ID, Name: p.Name}, nil
}

// verifyAs is a LiveKit verifier that answers err, for tests not about the LiveKit read itself; the
// real read is tested against livekit-server --dev in internal/auth.
type verifyAs struct{ err error }

func (v verifyAs) VerifyLiveKit(context.Context, string, string, string) error { return v.err }

// Without a store or without a verifier, nothing could be stored proven, so the route is off.
func TestCreateProjectDisabled(t *testing.T) {
	for name, a := range map[string]*API{
		"no store":    {VerifyLiveKit: verifyAs{}},
		"no verifier": {NewProjects: &countProjectCreates{}},
	} {
		t.Run(name, func(t *testing.T) {
			w := httptest.NewRecorder()
			a.createProject(w, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(`{"name":"Production","livekitUrl":"http://127.0.0.1","livekitApiKey":"key","livekitApiSecret":"secret"}`)))
			if w.Code != http.StatusForbidden {
				t.Fatalf("status=%d, want 403", w.Code)
			}
			if c, ok := a.NewProjects.(*countProjectCreates); ok && c.calls != 0 {
				t.Fatal("stored a project with no verifier")
			}
		})
	}
}

// A secret that does not verify, or a URL that gives no LiveKit answer, is a 422 before the store is
// asked, so its 409s reach only a caller who holds the secret. The answer is fixed: the cause, which
// can name an address the guard refused, is never in it.
func TestCreateProjectAnswersTheVerifier(t *testing.T) {
	body := `{"name":"Production","livekitUrl":"wss://lk.example.com","livekitApiKey":"APIkey","livekitApiSecret":"test-secret"}`
	cause := errors.New("dial tcp 10.0.0.7:443: prohibited IP address")
	for _, tt := range []struct {
		name    string
		err     error
		status  int
		code    string
		message string
		stores  int
	}{
		{"verified", nil, http.StatusCreated, "", "", 1},
		{"secret refused", fmt.Errorf("%w: %w", store.ErrLiveKitSecretInvalid, cause), http.StatusUnprocessableEntity, "livekit_secret_invalid", "the LiveKit API secret did not verify for this key", 0},
		{"no LiveKit answer", fmt.Errorf("%w: %w", store.ErrLiveKitUnreachable, cause), http.StatusUnprocessableEntity, "livekit_unreachable", "could not reach this LiveKit URL", 0},
		{"an unclassified failure", cause, http.StatusUnprocessableEntity, "livekit_unreachable", "could not reach this LiveKit URL", 0},
	} {
		t.Run(tt.name, func(t *testing.T) {
			creator := &countProjectCreates{}
			a := &API{NewProjects: creator, VerifyLiveKit: verifyAs{tt.err}}
			w := httptest.NewRecorder()
			a.createProject(w, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(body)))
			var e struct{ Code, Message string }
			_ = json.Unmarshal(w.Body.Bytes(), &e)
			if w.Code != tt.status || e.Code != tt.code || e.Message != tt.message || creator.calls != tt.stores {
				t.Fatalf("= %d %s, %d stores; want %d %s %q, %d stores", w.Code, w.Body, creator.calls, tt.status, tt.code, tt.message, tt.stores)
			}
			if strings.Contains(w.Body.String(), "10.0.0.7") || strings.Contains(w.Body.String(), "test-secret") {
				t.Errorf("the answer holds the cause or the secret: %s", w.Body)
			}
		})
	}
}

// A bucket a project brings is checked by the same rule on both paths: its endpoint is kept as the
// host and port minio-go dials, its scheme as UseSSL, and it belongs to the new project.
func TestCheckProjectBucket(t *testing.T) {
	base := store.NewProject{OrganisationID: "org-a", Name: "Support", LiveKitURL: "wss://lk.example.com", LiveKitAPIKey: "APIkey", LiveKitAPISecret: "s"}
	ok := store.ProjectBucket{Endpoint: " https://s3.us-east-1.amazonaws.com/ ", Bucket: "acme-recordings", Region: " us-east-1 ", AccessKey: " AKIA ", SecretKey: " kept "}
	p := base
	p.Bucket = &ok
	got, err := CheckProject(p)
	if err != nil {
		t.Fatal(err)
	}
	want := store.ProjectBucket{OrganisationID: "org-a", ProjectID: "support", Endpoint: "s3.us-east-1.amazonaws.com", UseSSL: true, Bucket: "acme-recordings", Region: "us-east-1", AccessKey: "AKIA", SecretKey: " kept "}
	if *got.Bucket != want {
		t.Errorf("bucket = %+v, want %+v", *got.Bucket, want)
	}
	p.Bucket = &store.ProjectBucket{Endpoint: "http://seaweedfs:8333", Bucket: "recordings", AccessKey: "k", SecretKey: "s"}
	if got, err := CheckProject(p); err != nil || got.Bucket.Endpoint != "seaweedfs:8333" || got.Bucket.UseSSL {
		t.Errorf("http endpoint = %+v, %v", got.Bucket, err)
	}
	with := func(change func(*store.ProjectBucket)) store.NewProject {
		b := ok
		change(&b)
		p := base
		p.Bucket = &b
		return p
	}
	for _, tt := range []struct {
		name string
		in   store.NewProject
		want string
	}{
		{"every field missing", with(func(b *store.ProjectBucket) { *b = store.ProjectBucket{SecretKey: " "} }), "missing recordingBucket.endpoint, recordingBucket.bucket, recordingBucket.accessKeyId, recordingBucket.secretAccessKey"},
		{"other scheme", with(func(b *store.ProjectBucket) { b.Endpoint = "ftp://s3.example.com" }), "recordingBucket.endpoint must be"},
		{"no scheme", with(func(b *store.ProjectBucket) { b.Endpoint = "s3.example.com" }), "recordingBucket.endpoint must be"},
		{"a path", with(func(b *store.ProjectBucket) { b.Endpoint = "https://s3.example.com/acme" }), "recordingBucket.endpoint must be"},
		{"user information", with(func(b *store.ProjectBucket) { b.Endpoint = "https://u:p@s3.example.com" }), "recordingBucket.endpoint must be"},
		{"bucket name", with(func(b *store.ProjectBucket) { b.Bucket = "Acme_Recordings" }), "recordingBucket.bucket must be"},
		{"space in region", with(func(b *store.ProjectBucket) { b.Region = "us east" }), "recordingBucket.region must be"},
		{"space in access key", with(func(b *store.ProjectBucket) { b.AccessKey = "AK IA" }), "recordingBucket.accessKeyId must be"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			_, err := CheckProject(tt.in)
			var invalid *ProjectInputError
			if !errors.As(err, &invalid) || !strings.HasPrefix(err.Error(), tt.want) {
				t.Errorf("CheckProject = %v, want a ProjectInputError starting %q", err, tt.want)
			}
		})
	}
}

// verifyBucketAs is a bucket verifier that answers err and counts its calls; the real write and
// delete are tested against SeaweedFS in internal/bucket.
type verifyBucketAs struct {
	err   error
	calls *int
}

func (v verifyBucketAs) VerifyBucket(context.Context, store.ProjectBucket) error {
	*v.calls++
	return v.err
}

// A bucket can never be changed once the project exists, so a project that brings one is stored
// only after the bucket took a write and a delete. A project without one never asks the bucket, and
// an install with no bucket verifier refuses one rather than store it unproven.
func TestCreateProjectAnswersTheBucketVerifier(t *testing.T) {
	const lk = `"name":"Support","livekitUrl":"wss://lk.example.com","livekitApiKey":"APIkey","livekitApiSecret":"test-secret"`
	withBucket := `{` + lk + `,"recordingBucket":{"endpoint":"https://s3.example.com","bucket":"acme-recordings","accessKeyId":"AKIA","secretAccessKey":"bucket-secret"}}`
	cause := errors.New("dial tcp 10.0.0.7:443: prohibited IP address")
	for _, tt := range []struct {
		name     string
		body     string
		err      error
		verifier bool
		status   int
		code     string
		checks   int
		stores   int
	}{
		{"verified", withBucket, nil, true, http.StatusCreated, "", 1, 1},
		{"no bucket brought", `{` + lk + `}`, nil, true, http.StatusCreated, "", 0, 1},
		{"refused", withBucket, fmt.Errorf("%w: AccessDenied", store.ErrBucketRefused), true, http.StatusUnprocessableEntity, "recording_bucket_refused", 1, 0},
		{"no S3 answer", withBucket, fmt.Errorf("%w: %w", store.ErrBucketUnreachable, cause), true, http.StatusUnprocessableEntity, "recording_bucket_unreachable", 1, 0},
		{"no bucket verifier", withBucket, nil, false, http.StatusForbidden, "forbidden", 0, 0},
	} {
		t.Run(tt.name, func(t *testing.T) {
			creator, checks := &countProjectCreates{}, 0
			a := &API{NewProjects: creator, VerifyLiveKit: verifyAs{}}
			if tt.verifier {
				a.VerifyBucket = verifyBucketAs{tt.err, &checks}
			}
			w := httptest.NewRecorder()
			a.createProject(w, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(tt.body)))
			var e struct{ Code string }
			_ = json.Unmarshal(w.Body.Bytes(), &e)
			if w.Code != tt.status || e.Code != tt.code || checks != tt.checks || creator.calls != tt.stores {
				t.Fatalf("= %d %s, %d checks, %d stores; want %d %s, %d, %d", w.Code, w.Body, checks, creator.calls, tt.status, tt.code, tt.checks, tt.stores)
			}
			if tt.name == "verified" && (creator.last.Bucket == nil || creator.last.Bucket.Endpoint != "s3.example.com" || creator.last.Bucket.SecretKey != "bucket-secret") {
				t.Errorf("stored bucket = %+v", creator.last.Bucket)
			}
			if strings.Contains(w.Body.String(), "10.0.0.7") || strings.Contains(w.Body.String(), "bucket-secret") {
				t.Errorf("the answer holds the cause or the secret: %s", w.Body)
			}
		})
	}
}

type countRooms struct{ n int }

func (countRooms) VerifyLiveKit(context.Context, string, string, string) error { return nil }
func (c countRooms) VerifyLiveKitRooms(context.Context, string, string, string) (int, error) {
	return c.n, nil
}

// createProject answers the rooms the verify read listed, and null, not zero, when the verifier
// cannot count them.
func TestCreateProjectAnswersLiveRooms(t *testing.T) {
	body := `{"name":"Production","livekitUrl":"wss://lk.example.com","livekitApiKey":"APIkey","livekitApiSecret":"test-secret"}`
	for _, tt := range []struct {
		name     string
		verifier LiveKitVerifier
		want     string
	}{
		{"counted", countRooms{3}, `"liveRooms":3`},
		{"none live", countRooms{0}, `"liveRooms":0`},
		{"not counted", verifyAs{nil}, `"liveRooms":null`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			a := &API{NewProjects: &countProjectCreates{}, VerifyLiveKit: tt.verifier}
			w := httptest.NewRecorder()
			a.createProject(w, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(body)))
			if w.Code != http.StatusCreated || !strings.Contains(w.Body.String(), tt.want) {
				t.Fatalf("= %d %s; want 201 with %s", w.Code, w.Body, tt.want)
			}
		})
	}
}
