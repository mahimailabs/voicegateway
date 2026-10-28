package scrape

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"time"

	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/common/expfmt"
	"github.com/prometheus/common/model"
)

// containerResource is one explicitly selected cgroup, not a host total or a sum of
// parent/child cgroups. Counters remain available for reset-aware rate calculation.
type containerResource struct {
	ID               string
	ObservedAt       time.Time
	StartedAt        *float64
	CPUSeconds       *float64
	CPUCores         *float64
	MemoryBytes      *float64
	MemoryLimitBytes *float64
}

// parseContainerResource uses the maintained Prometheus parser and an exact cAdvisor id.
// Never select a container by a mutable display name or accidentally include the root cgroup.
func parseContainerResource(body []byte, id string, now time.Time) (containerResource, error) {
	out := containerResource{ID: id}
	if id == "" || id == "/" {
		return out, errors.New("a non-root container id is required")
	}
	parser := expfmt.NewTextParser(model.UTF8Validation)
	families, err := parser.TextToMetricFamilies(bytes.NewReader(body))
	if err != nil {
		return out, errors.New("invalid resource metrics")
	}
	values := make(map[string]float64)
	cpuParts := make(map[string]float64)
	var observedMS *int64
	names := []string{"container_cpu_usage_seconds_total", "container_memory_working_set_bytes", "container_spec_cpu_quota", "container_spec_cpu_period", "container_spec_memory_limit_bytes", "container_start_time_seconds", "container_last_seen"}
	for _, name := range names {
		family := families[name]
		if family == nil {
			continue
		}
		for _, metric := range family.Metric {
			labels := make(map[string]string)
			for _, label := range metric.Label {
				labels[label.GetName()] = label.GetValue()
			}
			if labels["id"] != id {
				continue
			}
			key := name
			if name == "container_cpu_usage_seconds_total" {
				key += ":" + labels["cpu"]
			}
			if _, exists := values[key]; exists {
				return out, fmt.Errorf("ambiguous resource metric %s", name)
			}
			if name == "container_cpu_usage_seconds_total" || name == "container_memory_working_set_bytes" {
				if metric.TimestampMs == nil {
					return out, errors.New("resource observation timestamp unavailable")
				}
				if observedMS != nil && *observedMS != *metric.TimestampMs {
					return out, errors.New("inconsistent resource timestamps")
				}
				observedMS = metric.TimestampMs
			}
			var value float64
			if name == "container_cpu_usage_seconds_total" {
				if family.GetType() != dto.MetricType_COUNTER || metric.Counter == nil {
					return out, errors.New("CPU usage must be a counter")
				}
				value = metric.Counter.GetValue()
			} else {
				if family.GetType() != dto.MetricType_GAUGE || metric.Gauge == nil {
					return out, errors.New("resource measurement must be a gauge")
				}
				value = metric.Gauge.GetValue()
			}
			if math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
				return out, errors.New("invalid resource measurement")
			}
			values[key] = value
			if name == "container_cpu_usage_seconds_total" {
				cpuParts[labels["cpu"]] = value
			}
		}
	}
	seen, ok := values["container_last_seen"]
	if !ok || seen <= 0 {
		return out, errors.New("container observation unavailable")
	}
	// Bound before converting floating point timestamps to int64.
	current := float64(now.UnixNano()) / 1e9
	if seen > current+5 || seen < current-45 {
		return out, errors.New("container observation stale or invalid")
	}
	out.ObservedAt = time.Unix(0, int64(seen*1e9))
	if observedMS != nil {
		observed := time.UnixMilli(*observedMS)
		if observed.After(now.Add(5*time.Second)) || observed.Before(now.Add(-45*time.Second)) {
			return out, errors.New("resource measurement stale or invalid")
		}
		out.ObservedAt = observed
	}
	if len(cpuParts) > 0 {
		total, hasTotal := cpuParts["total"]
		unlabelled, hasUnlabelled := cpuParts[""]
		if hasTotal && hasUnlabelled {
			return out, errors.New("ambiguous CPU total")
		}
		if hasUnlabelled {
			total = unlabelled
		} else if !hasTotal {
			for label, value := range cpuParts {
				if len(label) < 4 || label[:3] != "cpu" {
					return out, errors.New("unknown CPU series")
				}
				for _, digit := range label[3:] {
					if digit < '0' || digit > '9' {
						return out, errors.New("invalid CPU series")
					}
				}
				total += value
			}
		}
		if math.IsInf(total, 0) {
			return out, errors.New("invalid CPU total")
		}
		values["container_cpu_usage_seconds_total"] = total
	}
	ptr := func(name string) *float64 {
		if value, ok := values[name]; ok {
			return &value
		}
		return nil
	}
	out.CPUSeconds = ptr("container_cpu_usage_seconds_total")
	out.StartedAt = ptr("container_start_time_seconds")
	out.MemoryBytes = ptr("container_memory_working_set_bytes")
	if quota, ok := values["container_spec_cpu_quota"]; ok && quota > 0 {
		if period := values["container_spec_cpu_period"]; period > 0 {
			cores := quota / period
			if !math.IsInf(cores, 0) {
				out.CPUCores = &cores
			}
		}
	}
	if limit := values["container_spec_memory_limit_bytes"]; limit > 0 {
		out.MemoryLimitBytes = &limit
	}
	return out, nil
}

// containerCPURate is cores consumed, not percent of the host. No rate bridges a
// stale interval, a container restart, counter reset, or missing start identity.
func containerCPURate(previous, current containerResource) *float64 {
	if previous.ID != current.ID || previous.CPUSeconds == nil || current.CPUSeconds == nil || previous.StartedAt == nil || current.StartedAt == nil || *previous.StartedAt != *current.StartedAt {
		return nil
	}
	elapsed := current.ObservedAt.Sub(previous.ObservedAt).Seconds()
	delta := *current.CPUSeconds - *previous.CPUSeconds
	if elapsed <= 0 || elapsed > 45 || delta < 0 {
		return nil
	}
	rate := delta / elapsed
	if math.IsNaN(rate) || math.IsInf(rate, 0) {
		return nil
	}
	return &rate
}
