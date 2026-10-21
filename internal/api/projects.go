package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"
	"github.com/minio/minio-go/v7/pkg/s3utils"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ProjectCreator creates a project with its LiveKit secret sealed: pgstore.Store.CreateProject, the
// path voicegateway project add takes too. Its errors are store.ErrProjectExists,
// store.ErrNotFound for an unknown organisation.
type ProjectCreator interface {
	CreateProject(ctx context.Context, p store.NewProject) (store.Project, error)
}

// LiveKitVerifier checks the supplied endpoint with a signed read before storing a project
// (scrape.Verifier). Its errors are store.ErrLiveKitSecretInvalid and store.ErrLiveKitUnreachable.
type LiveKitVerifier interface {
	VerifyLiveKit(ctx context.Context, url, apiKey, apiSecret string) error
}

// BucketVerifier proves a bucket a new project brings for its recordings takes the writes and deletes
// recordings need (bucket.Verifier). Its errors are store.ErrBucketRefused and
// store.ErrBucketUnreachable.
type BucketVerifier interface {
	VerifyBucket(ctx context.Context, b store.ProjectBucket) error
}

// maxProjectBody bounds a createProject body, which holds a name, a URL, a key and a secret, and
// optionally a bucket's endpoint, name, region, key and secret.
const maxProjectBody = 8 << 10

// Contract Error codes for createProject.
const (
	codeProjectExists = "project_exists"
	// The two answers of a LiveKit secret that could not be shown to verify, both 422.
	codeLiveKitSecretInvalid = "livekit_secret_invalid"
	codeLiveKitUnreachable   = "livekit_unreachable"
	// The two answers of a recording bucket that could not be shown to take writes and deletes, both 422.
	codeBucketRefused     = "recording_bucket_refused"
	codeBucketUnreachable = "recording_bucket_unreachable"
)

// The fields of a new project, by their contract names; ProjectInputError names them this way, and
// voicegateway project add maps them to its flags.
const (
	FieldName             = "name"
	FieldID               = "id"
	FieldLiveKitURL       = "livekitUrl"
	FieldLiveKitAPIKey    = "livekitApiKey"
	FieldLiveKitAPISecret = "livekitApiSecret"
	// The fields of a recording bucket a project brings, under recordingBucket.
	FieldBucketEndpoint  = "recordingBucket.endpoint"
	FieldBucketName      = "recordingBucket.bucket"
	FieldBucketRegion    = "recordingBucket.region"
	FieldBucketAccessKey = "recordingBucket.accessKeyId"
	FieldBucketSecretKey = "recordingBucket.secretAccessKey"
)

// ProjectInputError says which fields of a new project are missing, or which one breaks its rule.
type ProjectInputError struct {
	// Missing are the empty fields, in the order the form and the command line ask for them.
	Missing []string
	// Field breaks Rule; both are empty when only fields are missing.
	Field, Rule string
}

func (e *ProjectInputError) Error() string { return e.Describe(nil) }

// Describe is the error with each field under the caller's own name for it, such as a flag; a field
// names maps nothing for keeps its contract name.
func (e *ProjectInputError) Describe(names map[string]string) string {
	name := func(f string) string {
		if n, ok := names[f]; ok {
			return n
		}
		return f
	}
	if len(e.Missing) > 0 {
		out := make([]string, len(e.Missing))
		for i, f := range e.Missing {
			out[i] = name(f)
		}
		return "missing " + strings.Join(out, ", ")
	}
	return name(e.Field) + " " + e.Rule
}

// projectIDPattern is a project's URL id, as the projects table checks it.
var projectIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

var nonSlug = regexp.MustCompile(`[^a-z0-9]+`)

// Slug turns a project name into its URL id, the same way the console does: "Default project"
// becomes "default-project". It is cut to the 63 characters a project id may have.
func Slug(name string) string {
	s := strings.Trim(nonSlug.ReplaceAllString(strings.ToLower(name), "-"), "-")
	if len(s) > 63 {
		s = strings.TrimRight(s[:63], "-")
	}
	return s
}

// Limits on what a new project's fields may hold, so a request cannot store an unbounded row.
const (
	maxProjectName = 64
	maxLiveKitURL  = 2048
	maxLiveKitKey  = 256
)

// CheckProject is the one rule for a new project, shared by createProject and voicegateway project
// add. Every field is required. The name is trimmed, then 1 to 64 characters with no control
// characters and at least one letter or digit, so it has an id. The id, when empty, is the name's
// Slug. The LiveKit URL parses with the scheme ws, wss, http or https and a host. The key is trimmed
// and at most 256 characters with no spaces or control characters. The secret is kept exactly as
// given; it only has to be more than blanks. The error is a *ProjectInputError.
func CheckProject(p store.NewProject) (store.NewProject, error) {
	p.Name = strings.TrimSpace(p.Name)
	p.LiveKitURL = strings.TrimSpace(p.LiveKitURL)
	p.LiveKitAPIKey = strings.TrimSpace(p.LiveKitAPIKey)
	var missing []string
	for _, f := range []struct{ name, value string }{
		{FieldName, p.Name}, {FieldLiveKitURL, p.LiveKitURL}, {FieldLiveKitAPIKey, p.LiveKitAPIKey}, {FieldLiveKitAPISecret, strings.TrimSpace(p.LiveKitAPISecret)},
	} {
		if f.value == "" {
			missing = append(missing, f.name)
		}
	}
	if len(missing) > 0 {
		return store.NewProject{}, &ProjectInputError{Missing: missing}
	}
	if utf8.RuneCountInString(p.Name) > maxProjectName || strings.ContainsFunc(p.Name, unicode.IsControl) || (p.ID == "" && Slug(p.Name) == "") {
		return store.NewProject{}, &ProjectInputError{Field: FieldName, Rule: "must be 1 to 64 characters, with no control characters and at least one letter or digit"}
	}
	if p.ID == "" {
		p.ID = Slug(p.Name)
	}
	if !projectIDPattern.MatchString(p.ID) {
		return store.NewProject{}, &ProjectInputError{Field: FieldID, Rule: "must be 1 to 63 characters of a to z, 0 to 9 and -, starting with a letter or digit"}
	}
	if !liveKitURL(p.LiveKitURL) {
		return store.NewProject{}, &ProjectInputError{Field: FieldLiveKitURL, Rule: "must be a ws, wss, http or https URL with a host"}
	}
	if len(p.LiveKitAPIKey) > maxLiveKitKey || !plainToken(p.LiveKitAPIKey) {
		return store.NewProject{}, &ProjectInputError{Field: FieldLiveKitAPIKey, Rule: "must be at most 256 characters, with no spaces or control characters"}
	}
	if p.Bucket != nil {
		b, err := checkBucket(*p.Bucket)
		if err != nil {
			return store.NewProject{}, err
		}
		b.OrganisationID, b.ProjectID = p.OrganisationID, p.ID
		p.Bucket = &b
	}
	return p, nil
}

// plainToken reports whether s has no spaces or control characters.
func plainToken(s string) bool {
	return !strings.ContainsFunc(s, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsControl(r) })
}

// maxBucketField bounds a recording bucket's region and access key.
const maxBucketField = 256

// checkBucket is the rule for a recording bucket a new project brings. Endpoint, bucket, access key
// and secret are required; the region may be empty. The endpoint is an http or https URL with a
// host and nothing after it, kept as the host and port minio-go dials, with its scheme as UseSSL.
// The bucket name follows S3's rules (minio-go's s3utils). The region and access key are trimmed and
// at most 256 characters with no spaces or control characters. The secret is kept exactly as given.
func checkBucket(b store.ProjectBucket) (store.ProjectBucket, error) {
	b.Endpoint, b.Bucket = strings.TrimSpace(b.Endpoint), strings.TrimSpace(b.Bucket)
	b.Region, b.AccessKey = strings.TrimSpace(b.Region), strings.TrimSpace(b.AccessKey)
	var missing []string
	for _, f := range []struct{ name, value string }{
		{FieldBucketEndpoint, b.Endpoint}, {FieldBucketName, b.Bucket}, {FieldBucketAccessKey, b.AccessKey}, {FieldBucketSecretKey, strings.TrimSpace(b.SecretKey)},
	} {
		if f.value == "" {
			missing = append(missing, f.name)
		}
	}
	if len(missing) > 0 {
		return store.ProjectBucket{}, &ProjectInputError{Missing: missing}
	}
	u, err := url.Parse(b.Endpoint)
	if err != nil || len(b.Endpoint) > maxLiveKitURL || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" ||
		u.User != nil || strings.Trim(u.Path, "/") != "" || u.RawQuery != "" || u.Fragment != "" {
		return store.ProjectBucket{}, &ProjectInputError{Field: FieldBucketEndpoint, Rule: "must be an http or https URL with a host and no path, such as https://s3.us-east-1.amazonaws.com"}
	}
	b.Endpoint, b.UseSSL = u.Host, u.Scheme == "https"
	if s3utils.CheckValidBucketNameStrict(b.Bucket) != nil {
		return store.ProjectBucket{}, &ProjectInputError{Field: FieldBucketName, Rule: "must be a valid S3 bucket name: 3 to 63 characters of a to z, 0 to 9, . and -"}
	}
	if len(b.Region) > maxBucketField || !plainToken(b.Region) {
		return store.ProjectBucket{}, &ProjectInputError{Field: FieldBucketRegion, Rule: "must be at most 256 characters, with no spaces or control characters"}
	}
	if len(b.AccessKey) > maxBucketField || !plainToken(b.AccessKey) {
		return store.ProjectBucket{}, &ProjectInputError{Field: FieldBucketAccessKey, Rule: "must be at most 256 characters, with no spaces or control characters"}
	}
	return b, nil
}

// liveKitURL reports whether raw is a URL the LiveKit server SDK can dial: ws, wss, http or https,
// with a host and no user information.
func liveKitURL(raw string) bool {
	if len(raw) > maxLiveKitURL {
		return false
	}
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || u.User != nil {
		return false
	}
	switch u.Scheme {
	case "ws", "wss", "http", "https":
		return true
	}
	return false
}

// createProject serves operationId createProject: an Admin adds a LiveKit project to the
// organisation, through the same check, LiveKit verification and create path as voicegateway
// project add. The secret is sealed by the store and never answered or logged.
func (a *API) createProject(w http.ResponseWriter, r *http.Request) {
	// Without a verifier no key could be proven, so the route stays off rather than store one unproven.
	if a.NewProjects == nil || a.VerifyLiveKit == nil {
		writeError(w, r, http.StatusForbidden, "forbidden", "adding projects from the console is off on this install")
		return
	}
	var body struct {
		Name             string `json:"name"`
		LiveKitURL       string `json:"livekitUrl"`
		LiveKitAPIKey    string `json:"livekitApiKey"`
		LiveKitAPISecret string `json:"livekitApiSecret"`
		RecordingBucket  *struct {
			Endpoint        string `json:"endpoint"`
			Bucket          string `json:"bucket"`
			Region          string `json:"region"`
			AccessKeyID     string `json:"accessKeyId"`
			SecretAccessKey string `json:"secretAccessKey"`
		} `json:"recordingBucket"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxProjectBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "the body must be one JSON object of name, livekitUrl, livekitApiKey, livekitApiSecret and an optional recordingBucket, at most 8 KiB")
		return
	}
	var trailing any
	if err := dec.Decode(&trailing); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "the body must be one JSON object at most 8 KiB")
		return
	}
	org := chi.URLParam(r, "orgId")
	np := store.NewProject{
		OrganisationID: org, Name: body.Name, LiveKitURL: body.LiveKitURL, LiveKitAPIKey: body.LiveKitAPIKey, LiveKitAPISecret: body.LiveKitAPISecret,
	}
	if b := body.RecordingBucket; b != nil {
		np.Bucket = &store.ProjectBucket{Endpoint: b.Endpoint, Bucket: b.Bucket, Region: b.Region, AccessKey: b.AccessKeyID, SecretKey: b.SecretAccessKey}
	}
	in, err := CheckProject(np)
	if err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
		return
	}
	if in.Bucket != nil && a.VerifyBucket == nil {
		writeError(w, r, http.StatusForbidden, "forbidden", "bringing a recording bucket is off on this install")
		return
	}
	// One signed LiveKit read first: a key is stored, and its 409 answered, only for a caller who
	// holds its secret. API keys are not secret, so without this anyone could claim another
	// customer's key with a made-up secret, or learn from the 409 which keys this server holds. The
	// cause is not logged or answered: it can name addresses the guard refused.
	switch err := a.VerifyLiveKit.VerifyLiveKit(r.Context(), in.LiveKitURL, in.LiveKitAPIKey, in.LiveKitAPISecret); {
	case errors.Is(err, store.ErrLiveKitSecretInvalid):
		writeError(w, r, http.StatusUnprocessableEntity, codeLiveKitSecretInvalid, "the LiveKit API secret did not verify for this key")
		return
	case err != nil:
		writeError(w, r, http.StatusUnprocessableEntity, codeLiveKitUnreachable, "could not reach this LiveKit URL")
		return
	}
	// The bucket cannot be changed once the project exists, so it is proven now: a write and a
	// delete, what storing and expiring recordings need. As with LiveKit, the cause is not answered.
	if in.Bucket != nil {
		switch err := a.VerifyBucket.VerifyBucket(r.Context(), *in.Bucket); {
		case errors.Is(err, store.ErrBucketRefused):
			writeError(w, r, http.StatusUnprocessableEntity, codeBucketRefused, "the S3 endpoint refused a write or delete with this access key, secret and bucket name")
			return
		case err != nil:
			writeError(w, r, http.StatusUnprocessableEntity, codeBucketUnreachable, "could not reach this S3 endpoint")
			return
		}
	}
	p, err := a.NewProjects.CreateProject(r.Context(), in)
	switch {
	case errors.Is(err, store.ErrProjectExists):
		writeError(w, r, http.StatusConflict, codeProjectExists, fmt.Sprintf("this organisation already has a project with the id %s; choose another name", in.ID))
		return
	case err != nil:
		a.failWith(w, r, err, "organisation not found", "the project could not be stored")
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusCreated, workspaceProject{ID: p.ID, Name: p.Name})
}
