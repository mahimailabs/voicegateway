// Package deploy holds the Compose stack and image build; this test keeps every pulled image pinned.
package deploy

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

var digest = regexp.MustCompile(`@sha256:[0-9a-f]{64}$`)

func TestComposeImagesPinned(t *testing.T) {
	raw, err := os.ReadFile("compose.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var images, unpinned []string
	for line := range strings.SplitSeq(string(raw), "\n") {
		line = strings.TrimSpace(line)
		image, ok := strings.CutPrefix(line, "image: ")
		if !ok || strings.HasPrefix(image, "${VOICEGATEWAY_IMAGE:-voicegateway:") { // built from deploy/Dockerfile unless set
			continue
		}
		images = append(images, image)
		if !digest.MatchString(image) {
			unpinned = append(unpinned, image)
		}
	}
	if len(images) < 4 {
		t.Fatalf("found %d pulled images, want at least the four dependencies", len(images))
	}
	if len(unpinned) > 0 {
		t.Errorf("images without a sha256 digest: %v", unpinned)
	}

	docker, err := os.ReadFile("Dockerfile")
	if err != nil {
		t.Fatal(err)
	}
	for line := range strings.SplitSeq(string(docker), "\n") {
		if from, ok := strings.CutPrefix(strings.TrimSpace(line), "FROM "); ok {
			image := strings.Fields(from)[0]
			if !digest.MatchString(image) {
				t.Errorf("Dockerfile base image without a digest: %s", image)
			}
		}
	}
}
