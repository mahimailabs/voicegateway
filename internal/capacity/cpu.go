package capacity

import (
	"fmt"

	"github.com/shirou/gopsutil/v4/cpu"
)

// HostCPU is the tester's CPU: how busy the machine the worker runs on has been, across all its
// cores, since the previous call (gopsutil, /proc/stat on Linux). In a container it is the host's,
// which is what the callers share.
func HostCPU() (float64, error) {
	pct, err := cpu.Percent(0, false)
	if err != nil {
		return 0, fmt.Errorf("tester cpu: %w", err)
	}
	if len(pct) == 0 {
		return 0, fmt.Errorf("tester cpu: no reading")
	}
	return pct[0], nil
}
