package scrape

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

func resourceFixture(now time.Time) string {
	fixture := fmt.Sprintf(`# TYPE container_last_seen gauge
container_last_seen{id="/docker/worker"} %d
# TYPE container_start_time_seconds gauge
container_start_time_seconds{id="/docker/worker"} 1700000000
# TYPE container_cpu_usage_seconds_total counter
container_cpu_usage_seconds_total{id="/docker/worker",cpu="total"} 30
container_cpu_usage_seconds_total{id="/docker/worker",cpu="cpu00"} 20
container_cpu_usage_seconds_total{id="/docker/other",cpu="total"} 900
# TYPE container_memory_working_set_bytes gauge
container_memory_working_set_bytes{id="/docker/worker"} 1048576
container_memory_working_set_bytes{id="/"} 99999999
# TYPE container_spec_cpu_quota gauge
container_spec_cpu_quota{id="/docker/worker"} 200000
# TYPE container_spec_cpu_period gauge
container_spec_cpu_period{id="/docker/worker"} 100000
# TYPE container_spec_memory_limit_bytes gauge
container_spec_memory_limit_bytes{id="/docker/worker"} 4194304
`, now.Unix())
	lines := strings.Split(fixture, "\n")
	for i, line := range lines {
		if strings.HasPrefix(line, "container_cpu_usage_seconds_total{") || strings.HasPrefix(line, "container_memory_working_set_bytes{") {
			lines[i] = fmt.Sprintf("%s %d", line, now.UnixMilli())
		}
	}
	return strings.Join(lines, "\n")
}

func TestContainerResourceExactMapping(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	got, err := parseContainerResource([]byte(resourceFixture(now)), "/docker/worker", now)
	if err != nil {
		t.Fatal(err)
	}
	if got.CPUSeconds == nil || *got.CPUSeconds != 30 || got.CPUCores == nil || *got.CPUCores != 2 || got.MemoryBytes == nil || *got.MemoryBytes != 1048576 || got.MemoryLimitBytes == nil || *got.MemoryLimitBytes != 4194304 {
		t.Fatalf("incorrect selected container: %+v", got)
	}
	for _, id := range []string{"", "/", "/docker/missing"} {
		if _, err := parseContainerResource([]byte(resourceFixture(now)), id, now); err == nil {
			t.Fatalf("accepted missing or root id %q", id)
		}
	}
}

func TestContainerResourceMissingAndInvalid(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	fixture := resourceFixture(now)
	for _, tc := range []struct{ name, body string }{
		{"stale", resourceFixture(now.Add(-time.Minute))},
		{"future", resourceFixture(now.Add(time.Minute))},
		{"duplicate", fixture + fmt.Sprintf("container_last_seen{id=\"/docker/worker\",name=\"other\"} %d\n", now.Unix())},
		{"nan", strings.Replace(fixture, "} 1048576", "} NaN", 1)},
		{"negative", strings.Replace(fixture, "} 1048576", "} -1", 1)},
		{"counter type", strings.Replace(fixture, "seconds_total counter", "seconds_total gauge", 1)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := parseContainerResource([]byte(tc.body), "/docker/worker", now); err == nil {
				t.Fatal("accepted invalid observation")
			}
		})
	}
	minimal := fmt.Sprintf("# TYPE container_last_seen gauge\ncontainer_last_seen{id=\"/docker/worker\"} %d\n", now.Unix())
	got, err := parseContainerResource([]byte(minimal), "/docker/worker", now)
	if err != nil {
		t.Fatal(err)
	}
	if got.CPUSeconds != nil || got.CPUCores != nil || got.MemoryBytes != nil || got.MemoryLimitBytes != nil {
		t.Fatal("missing resource became zero")
	}
	unlimited := strings.Replace(strings.Replace(fixture, "} 200000", "} 0", 1), "} 4194304", "} 0", 1)
	got, err = parseContainerResource([]byte(unlimited), "/docker/worker", now)
	if err != nil || got.CPUCores != nil || got.MemoryLimitBytes != nil {
		t.Fatal("unlimited allocation became zero", err)
	}
}

func TestContainerCPURateIdentityAndResets(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	previous, err := parseContainerResource([]byte(resourceFixture(now)), "/docker/worker", now)
	if err != nil {
		t.Fatal(err)
	}
	current := previous
	current.ObservedAt = now.Add(15 * time.Second)
	cpu := 45.0
	current.CPUSeconds = &cpu
	if rate := containerCPURate(previous, current); rate == nil || *rate != 1 {
		t.Fatal("expected one CPU core used")
	}
	for _, tc := range []struct {
		name   string
		change func(*containerResource)
	}{
		{"reset", func(s *containerResource) { v := 1.0; s.CPUSeconds = &v }},
		{"restart", func(s *containerResource) { v := 1700000001.0; s.StartedAt = &v }},
		{"unknown start", func(s *containerResource) { s.StartedAt = nil }},
		{"gap", func(s *containerResource) { s.ObservedAt = now.Add(time.Minute) }},
		{"same time", func(s *containerResource) { s.ObservedAt = now }},
		{"other container", func(s *containerResource) { s.ID = "/docker/other" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			value := current
			tc.change(&value)
			if containerCPURate(previous, value) != nil {
				t.Fatal("invented a rate")
			}
		})
	}
}

func TestContainerResourceUsesMeasurementTime(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	fixture := resourceFixture(now.Add(-time.Minute))
	fixture = strings.Replace(fixture, fmt.Sprintf("} %d\n", now.Add(-time.Minute).Unix()), fmt.Sprintf("} %d\n", now.Unix()), 1)
	if _, err := parseContainerResource([]byte(fixture), "/docker/worker", now); err == nil {
		t.Fatal("fresh exporter hid stale measurement")
	}
	fixture = resourceFixture(now)
	lines := strings.Split(fixture, "\n")
	for i, line := range lines {
		if strings.Contains(line, `cpu="total"`) {
			lines[i] = ""
		}
	}
	got, err := parseContainerResource([]byte(strings.Join(lines, "\n")), "/docker/worker", now)
	if err != nil || got.CPUSeconds == nil || *got.CPUSeconds != 20 {
		t.Fatal("per-CPU series not measured correctly", err)
	}
}
