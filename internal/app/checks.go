package app

import (
	"context"
	"fmt"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/jackc/pgx/v5"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	"github.com/redis/go-redis/v9"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/preflight"
)

// go-redis logs dial failures itself, unstructured; preflight and the roles report them instead.
// SetLogger is global, so this silences go-redis logging for the whole process, every client and
// role included, not only the preflight check. Errors still reach the caller as returned values.
func init() { redis.SetLogger(quietRedis{}) }

type quietRedis struct{}

func (quietRedis) Printf(context.Context, string, ...any) {}

// Checks returns one preflight check per dependency. Each probe opens its own connection and
// closes it, so preflight never shares state with the roles.
func Checks(c config.Config) []preflight.Check {
	return []preflight.Check{
		{Name: "postgres", Target: config.Redact(c.PostgresURL), Probe: func(ctx context.Context) error {
			conn, err := pgx.Connect(ctx, c.PostgresURL)
			if err != nil {
				return fmt.Errorf("connect: %w", redactErr(err, c.PostgresURL))
			}
			defer func() { _ = conn.Close(context.WithoutCancel(ctx)) }()
			if err := conn.Ping(ctx); err != nil {
				return fmt.Errorf("ping: %w", err)
			}
			return nil
		}},
		{Name: "clickhouse", Target: config.Redact(c.ClickHouseURL), Probe: func(ctx context.Context) error {
			opts, err := clickhouse.ParseDSN(c.ClickHouseURL)
			if err != nil {
				return fmt.Errorf("parse VOICEGATEWAY_CLICKHOUSE_URL: %w", redactErr(err, c.ClickHouseURL))
			}
			conn, err := clickhouse.Open(opts)
			if err != nil {
				return fmt.Errorf("open: %w", err)
			}
			defer func() { _ = conn.Close() }()
			if err := conn.Ping(ctx); err != nil {
				return fmt.Errorf("ping: %w", err)
			}
			return nil
		}},
		{Name: "redis", Target: config.Redact(c.RedisURL), Probe: func(ctx context.Context) error {
			opts, err := redis.ParseURL(c.RedisURL)
			if err != nil {
				return fmt.Errorf("parse VOICEGATEWAY_REDIS_URL: %w", redactErr(err, c.RedisURL))
			}
			// One attempt per check: preflight reports a failure, it does not wait one out.
			opts.MaxRetries = -1
			client := redis.NewClient(opts)
			defer func() { _ = client.Close() }()
			if err := client.Ping(ctx).Err(); err != nil {
				return fmt.Errorf("ping: %w", err)
			}
			return nil
		}},
		{Name: "s3", Target: c.S3.Endpoint + "/" + c.S3.Bucket, Probe: func(ctx context.Context) error {
			client, err := minio.New(c.S3.Endpoint, &minio.Options{
				Creds:  credentials.NewStaticV4(c.S3.AccessKey, c.S3.SecretKey, ""),
				Secure: c.S3.UseSSL,
			})
			if err != nil {
				return fmt.Errorf("client: %w", err)
			}
			found, err := client.BucketExists(ctx, c.S3.Bucket)
			if err != nil {
				return fmt.Errorf("bucket %s: %w", c.S3.Bucket, err)
			}
			if !found {
				return fmt.Errorf("bucket %s does not exist", c.S3.Bucket)
			}
			return nil
		}},
	}
}

// redactErr drops an error whose text repeats the raw URL, so a password never reaches the report.
func redactErr(err error, raw string) error {
	if err == nil {
		return nil
	}
	if containsSecret(err.Error(), raw) {
		return fmt.Errorf("invalid URL %s", config.Redact(raw))
	}
	return err
}
