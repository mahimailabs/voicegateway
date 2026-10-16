package app

import (
	"bytes"
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/testcontainers/testcontainers-go"
	tcclickhouse "github.com/testcontainers/testcontainers-go/modules/clickhouse"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/preflight"
)

// The same images, tags and digests as deploy/compose.yaml, so the tests exercise what ships.
const (
	postgresImage   = "postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
	clickhouseImage = "clickhouse/clickhouse-server:26.1.12.23-alpine@sha256:369bd68942569e0effdf0aa68b4f844a74d97b73e281cf8012ce90e9865cc411"
	redisImage      = "redis:8.10.2-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0"
	s3Image         = "chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"
)

// stack is the four dependencies running in containers, and a config pointing at them.
type stack struct {
	cfg   config.Config
	redis *tcredis.RedisContainer
}

// needDocker skips with -short or without Docker on a laptop; in CI a missing Docker fails the test.
func needDocker(t *testing.T) {
	t.Helper()
	if os.Getenv("CI") != "" {
		return
	}
	if testing.Short() {
		t.Skip("needs Docker; runs in CI")
	}
	testcontainers.SkipIfProviderIsNotHealthy(t)
}

func startStack(t *testing.T) stack {
	t.Helper()
	ctx := context.Background()

	pg, err := tcpostgres.Run(ctx, postgresImage,
		tcpostgres.WithDatabase("voicegateway"), tcpostgres.WithUsername("voicegateway"), tcpostgres.WithPassword("pg-test"),
		tcpostgres.BasicWaitStrategies())
	testcontainers.CleanupContainer(t, pg)
	if err != nil {
		t.Fatalf("start postgres: %v", err)
	}
	pgURL, err := pg.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatal(err)
	}

	ch, err := tcclickhouse.Run(ctx, clickhouseImage,
		tcclickhouse.WithDatabase("voicegateway"), tcclickhouse.WithUsername("voicegateway"), tcclickhouse.WithPassword("ch-test"))
	testcontainers.CleanupContainer(t, ch)
	if err != nil {
		t.Fatalf("start clickhouse: %v", err)
	}
	chURL, err := ch.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}

	rd, err := tcredis.Run(ctx, redisImage)
	testcontainers.CleanupContainer(t, rd)
	if err != nil {
		t.Fatalf("start redis: %v", err)
	}
	rdURL, err := rd.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}

	s3, err := testcontainers.Run(ctx, s3Image,
		testcontainers.WithCmd("mini", "-dir=/data", "-ip.bind=0.0.0.0"),
		testcontainers.WithEnv(map[string]string{"AWS_ACCESS_KEY_ID": "vg-test", "AWS_SECRET_ACCESS_KEY": "s3-test", "S3_BUCKET": "recordings"}),
		testcontainers.WithExposedPorts("8333/tcp"),
		testcontainers.WithWaitStrategy(wait.ForListeningPort("8333/tcp").WithStartupTimeout(2*time.Minute)))
	testcontainers.CleanupContainer(t, s3)
	if err != nil {
		t.Fatalf("start s3: %v", err)
	}
	s3Endpoint, err := s3.PortEndpoint(ctx, "8333/tcp", "")
	if err != nil {
		t.Fatal(err)
	}

	return stack{
		redis: rd,
		cfg: config.Config{
			PostgresURL:   pgURL,
			ClickHouseURL: chURL,
			RedisURL:      rdURL,
			S3:            config.S3{Endpoint: s3Endpoint, Bucket: "recordings", AccessKey: "vg-test", SecretKey: "s3-test"},
		},
	}
}

func runPreflight(t *testing.T, c config.Config) (bool, string) {
	t.Helper()
	// SeaweedFS creates the bucket shortly after its port opens, so the check is retried briefly.
	var out bytes.Buffer
	var ok bool
	for range 30 {
		out.Reset()
		var err error
		ok, err = preflight.Report(&out, preflight.Preflight{Checks: Checks(c), Timeout: 5 * time.Second}.Run(context.Background()))
		if err != nil {
			t.Fatal(err)
		}
		if ok || !strings.Contains(out.String(), "FAIL s3") || strings.Count(out.String(), "FAIL") > 1 {
			break
		}
		time.Sleep(time.Second)
	}
	return ok, out.String()
}

func TestPreflightAllReachable(t *testing.T) {
	needDocker(t)
	s := startStack(t)

	ok, out := runPreflight(t, s.cfg)
	if !ok {
		t.Fatalf("preflight failed with every dependency up:\n%s", out)
	}
	for _, name := range []string{"postgres", "clickhouse", "redis", "s3"} {
		if !strings.Contains(out, "OK   "+name) {
			t.Errorf("no OK line for %s:\n%s", name, out)
		}
	}
	for _, secret := range []string{"pg-test", "ch-test", "s3-test"} {
		if strings.Contains(out, secret) {
			t.Errorf("report leaks %q:\n%s", secret, out)
		}
	}
}

func TestPreflightReportsEveryFailure(t *testing.T) {
	needDocker(t)
	s := startStack(t)
	runPreflight(t, s.cfg) // wait for the bucket before taking Redis away

	stop := time.Second
	if err := s.redis.Stop(context.Background(), &stop); err != nil {
		t.Fatalf("stop redis: %v", err)
	}
	ok, out := runPreflight(t, s.cfg)
	if ok {
		t.Fatalf("preflight passed with Redis stopped:\n%s", out)
	}
	if !strings.Contains(out, "FAIL redis") {
		t.Errorf("report does not name Redis:\n%s", out)
	}
	for _, name := range []string{"postgres", "clickhouse", "s3"} {
		if !strings.Contains(out, "OK   "+name) {
			t.Errorf("%s not reported while Redis is down:\n%s", name, out)
		}
	}
}
