package bucket

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ForProject returns the bucket a project brought, reached through transport: on a shared instance,
// one that refuses private and link-local addresses, since the owner chose the endpoint. It opens
// no connection.
func ForProject(b store.ProjectBucket, transport http.RoundTripper) (*Bucket, error) {
	client, err := minio.New(b.Endpoint, &minio.Options{
		Creds:     credentials.NewStaticV4(b.AccessKey, b.SecretKey, ""),
		Secure:    b.UseSSL,
		Region:    b.Region,
		Transport: transport,
	})
	if err != nil {
		return nil, fmt.Errorf("s3 client for %s/%s: %w", b.OrganisationID, b.ProjectID, err)
	}
	return &Bucket{client: client, name: b.Bucket}, nil
}

// checkKey is the object Verify writes and removes; it sits beside the day prefixes, so Expire never
// lists it as a day.
const checkKey = prefix + "voicegateway-check"

// Verifier proves a bucket a project brings takes the writes and deletes recordings need, before
// the project is stored: the bucket cannot be changed afterwards.
type Verifier struct {
	Transport http.RoundTripper
	// Timeout bounds the write and the delete together.
	Timeout time.Duration
}

// NewVerifier verifies through transport, the one ForProject dials through later.
func NewVerifier(transport http.RoundTripper) Verifier {
	return Verifier{Transport: transport, Timeout: 10 * time.Second}
}

// VerifyBucket writes an empty object to the bucket and removes it, two signed calls: storing a
// recording needs the one, retention the other. An S3 answer refusing either is
// store.ErrBucketRefused; anything else, a refused address included, is store.ErrBucketUnreachable,
// so the answer never says which addresses the guard refuses. The cause is wrapped for the command
// line; it never holds the secret, which only signs.
func (v Verifier) VerifyBucket(ctx context.Context, b store.ProjectBucket) error {
	bk, err := ForProject(b, v.Transport)
	if err != nil {
		return fmt.Errorf("%w: %w", store.ErrBucketUnreachable, err)
	}
	ctx, cancel := context.WithTimeout(ctx, v.Timeout)
	defer cancel()
	if _, err := bk.client.PutObject(ctx, bk.name, checkKey, bytes.NewReader(nil), 0, minio.PutObjectOptions{ContentType: "text/plain"}); err != nil {
		return refusedOrUnreachable(err)
	}
	if err := bk.client.RemoveObject(ctx, bk.name, checkKey, minio.RemoveObjectOptions{}); err != nil {
		return refusedOrUnreachable(err)
	}
	return nil
}

// refusedOrUnreachable tells an S3 answer from no answer: minio-go sets a status code only when the
// endpoint replied.
func refusedOrUnreachable(err error) error {
	if resp := minio.ToErrorResponse(err); resp.StatusCode >= 400 && resp.StatusCode < 500 {
		return fmt.Errorf("%w: %s", store.ErrBucketRefused, resp.Code)
	}
	return fmt.Errorf("%w: %w", store.ErrBucketUnreachable, err)
}

// ProjectBuckets finds the bucket a project brought: store.ErrNotFound when it uses the instance's.
type ProjectBuckets interface {
	ProjectBucket(ctx context.Context, organisationID, projectID string) (store.ProjectBucket, error)
	ProjectBuckets(ctx context.Context) ([]store.ProjectBucket, error)
}

// Buckets stores and serves each project's recordings in its own bucket when it brought one, and
// in the instance's otherwise. A bucket a project brought never changes, so its client is kept once
// made; the instance's is asked for each time, one indexed read, so nothing is remembered about a
// project that has none.
type Buckets struct {
	Default   *Bucket
	Projects  ProjectBuckets
	Transport http.RoundTripper

	mu  sync.Mutex
	own map[string]*Bucket
}

// For returns the bucket a project's recordings live in.
func (b *Buckets) For(ctx context.Context, organisationID, projectID string) (*Bucket, error) {
	id := organisationID + "/" + projectID
	b.mu.Lock()
	bk, ok := b.own[id]
	b.mu.Unlock()
	if ok {
		return bk, nil
	}
	own, err := b.Projects.ProjectBucket(ctx, organisationID, projectID)
	if errors.Is(err, store.ErrNotFound) {
		return b.Default, nil
	}
	if err != nil {
		return nil, err
	}
	if bk, err = ForProject(own, b.Transport); err != nil {
		return nil, err
	}
	b.mu.Lock()
	if b.own == nil {
		b.own = map[string]*Bucket{}
	}
	b.own[id] = bk
	b.mu.Unlock()
	return bk, nil
}

// PutRecording stores a recording in the project's bucket, as Bucket.PutRecording does.
func (b *Buckets) PutRecording(ctx context.Context, at time.Time, organisationID, projectID, jobID string, r io.Reader, size int64) (string, int64, error) {
	bk, err := b.For(ctx, organisationID, projectID)
	if err != nil {
		return "", 0, err
	}
	return bk.PutRecording(ctx, at, organisationID, projectID, jobID, r, size)
}

// Open opens a recording of the project in its bucket, or store.ErrNotFound.
func (b *Buckets) Open(ctx context.Context, organisationID, projectID, key string) (io.ReadSeekCloser, error) {
	bk, err := b.For(ctx, organisationID, projectID)
	if err != nil {
		return nil, err
	}
	return bk.Open(ctx, key)
}

// Expire removes expired recordings from the instance's bucket and from every bucket a project
// brought. One bucket that fails does not keep the others from expiring; its error is returned
// with theirs.
func (b *Buckets) Expire(ctx context.Context, cutoff time.Time) (int, error) {
	removed, err := b.Default.Expire(ctx, cutoff)
	errs := []error{err}
	own, err := b.Projects.ProjectBuckets(ctx)
	if err != nil {
		return removed, errors.Join(append(errs, err)...)
	}
	for _, p := range own {
		bk, err := ForProject(p, b.Transport)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		n, err := bk.Expire(ctx, cutoff)
		removed += n
		if err != nil {
			errs = append(errs, fmt.Errorf("bucket of %s/%s: %w", p.OrganisationID, p.ProjectID, err))
		}
	}
	return removed, errors.Join(errs...)
}
