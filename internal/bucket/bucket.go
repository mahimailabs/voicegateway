package bucket

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"iter"
	"path"
	"regexp"
	"strings"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// prefix is where every recording lives in the bucket.
const prefix = "recordings/"

// partSize bounds what one upload of unknown length holds in memory; 5 MiB is S3's smallest part.
const partSize = 5 << 20

// Bucket is the configured S3 bucket.
type Bucket struct {
	client *minio.Client
	name   string
}

// New returns the bucket VOICEGATEWAY_S3_* names. It opens no connection.
func New(c config.S3) (*Bucket, error) {
	client, err := minio.New(c.Endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(c.AccessKey, c.SecretKey, ""),
		Secure: c.UseSSL,
	})
	if err != nil {
		return nil, fmt.Errorf("s3 client: %w", err)
	}
	return &Bucket{client: client, name: c.Bucket}, nil
}

// safeName is a LiveKit job id as a key segment; anything else is replaced by a random name.
var safeName = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

// Key is the object key of a recording web stores at, for a project and job. A job id that is
// empty or not a plain name gets a random one, so a caller never chooses a path.
func Key(at time.Time, organisationID, projectID, jobID string) (string, error) {
	if !safeName.MatchString(jobID) {
		var b [16]byte
		if _, err := rand.Read(b[:]); err != nil {
			return "", fmt.Errorf("recording name: %w", err)
		}
		jobID = hex.EncodeToString(b[:])
	}
	return prefix + at.UTC().Format("2006/01/02") + "/" + path.Join(organisationID, projectID, jobID) + ".ogg", nil
}

// PutRecording stores r as the Ogg recording of a project's job, received at, and returns its key
// and size. size is -1 when unknown.
func (b *Bucket) PutRecording(ctx context.Context, at time.Time, organisationID, projectID, jobID string, r io.Reader, size int64) (string, int64, error) {
	key, err := Key(at, organisationID, projectID, jobID)
	if err != nil {
		return "", 0, err
	}
	info, err := b.client.PutObject(ctx, b.name, key, r, size, minio.PutObjectOptions{ContentType: "audio/ogg", PartSize: partSize})
	if err != nil {
		return "", 0, fmt.Errorf("store recording: %w", err)
	}
	return key, info.Size, nil
}

// Open returns the object at key to read and seek, ranged reads included, or store.ErrNotFound.
func (b *Bucket) Open(ctx context.Context, key string) (io.ReadSeekCloser, error) {
	obj, err := b.client.GetObject(ctx, b.name, key, minio.GetObjectOptions{})
	if err != nil {
		return nil, fmt.Errorf("open recording: %w", err)
	}
	// GetObject sends nothing until first use; Stat asks now, so a missing object is a 404 before
	// any byte is written.
	if _, err := obj.Stat(); err != nil {
		_ = obj.Close()
		if minio.ToErrorResponse(err).Code == minio.NoSuchKey {
			return nil, store.ErrNotFound
		}
		return nil, fmt.Errorf("open recording: %w", err)
	}
	return obj, nil
}

// Expire removes every recording stored on a UTC day that ended at or before cutoff, and returns
// how many objects it removed. A day younger than that is kept whole, so an object lives at most a
// day past cutoff.
func (b *Bucket) Expire(ctx context.Context, cutoff time.Time) (int, error) {
	days, err := b.days(ctx)
	if err != nil {
		return 0, err
	}
	removed := 0
	for _, day := range days {
		start, err := time.Parse("2006/01/02/", strings.TrimPrefix(day, prefix))
		if err != nil || start.AddDate(0, 0, 1).After(cutoff) {
			// Not a day this package wrote, or not yet expired.
			continue
		}
		n, err := b.removeAll(ctx, day)
		removed += n
		if err != nil {
			return removed, err
		}
	}
	return removed, nil
}

// days lists the recordings/YYYY/MM/DD/ prefixes in the bucket, three listings deep.
func (b *Bucket) days(ctx context.Context) ([]string, error) {
	level := []string{prefix}
	for range 3 {
		var next []string
		for _, p := range level {
			for o := range b.client.ListObjectsIter(ctx, b.name, minio.ListObjectsOptions{Prefix: p}) {
				if o.Err != nil {
					return nil, fmt.Errorf("list recordings under %s: %w", p, o.Err)
				}
				if strings.HasSuffix(o.Key, "/") {
					next = append(next, o.Key)
				}
			}
		}
		level = next
	}
	return level, nil
}

// removeAll removes every object under p in multi-object deletes.
func (b *Bucket) removeAll(ctx context.Context, p string) (int, error) {
	var listErr error
	objects := func(yield func(minio.ObjectInfo) bool) {
		for o := range b.client.ListObjectsIter(ctx, b.name, minio.ListObjectsOptions{Prefix: p, Recursive: true}) {
			if o.Err != nil {
				listErr = o.Err
				return
			}
			if !yield(o) {
				return
			}
		}
	}
	results, err := b.client.RemoveObjectsWithIter(ctx, b.name, iter.Seq[minio.ObjectInfo](objects), minio.RemoveObjectsOptions{})
	if err != nil {
		return 0, fmt.Errorf("remove recordings under %s: %w", p, err)
	}
	removed := 0
	for r := range results {
		if r.Err != nil {
			return removed, fmt.Errorf("remove recording %s: %w", r.ObjectName, r.Err)
		}
		removed++
	}
	if listErr != nil {
		return removed, fmt.Errorf("list recordings under %s: %w", p, listErr)
	}
	return removed, nil
}
