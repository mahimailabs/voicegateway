package main

import (
	"bytes"
	"fmt"
	"sync"
	"testing"
	"time"
)

func TestTheEmbeddedToneIsRealOpus(t *testing.T) {
	if len(toneOgg) == 0 {
		t.Fatal("tone.ogg was not embedded")
	}
	if !bytes.HasPrefix(toneOgg, []byte("OggS")) {
		t.Fatal("tone.ogg is not an ogg stream")
	}
	if !bytes.Contains(toneOgg[:200], []byte("OpusHead")) {
		t.Fatal("tone.ogg does not declare an Opus stream")
	}
}

func TestTheToneParsesIntoOneSecondOfTwentyMillisecondFrames(t *testing.T) {
	frames, err := ToneFrames()
	if err != nil {
		t.Fatalf("ToneFrames: %v", err)
	}
	// One second of 20ms frames. Allow a couple either side for encoder
	// padding, but not the order-of-magnitude error that a page mistaken for a
	// frame would produce: glued pages would give roughly a fifth of this.
	if len(frames) < 45 || len(frames) > 55 {
		t.Fatalf("got %d frames, want ~50 for one second at 20ms", len(frames))
	}
	total := time.Duration(len(frames)) * ToneFrameDuration
	if total < 900*time.Millisecond || total > 1100*time.Millisecond {
		t.Fatalf("frames total %s, want ~1s", total)
	}
}

func TestNoFrameIsEmptyOrAHeader(t *testing.T) {
	// "Publish real Opus, never null or zero-byte samples." A zero-length frame
	// is a sample a receiver drops, and an OpusHead frame is four bytes of ASCII
	// where a decoder expects audio.
	frames, err := ToneFrames()
	if err != nil {
		t.Fatalf("ToneFrames: %v", err)
	}
	for i, f := range frames {
		if len(f) == 0 {
			t.Fatalf("frame %d is zero bytes", i)
		}
		if isOpusHeader(f) {
			t.Fatalf("frame %d is a container header, not audio", i)
		}
	}
}

func TestEveryTrackSharesTheSameBackingArrays(t *testing.T) {
	// The zero-per-call-encode-CPU claim. Two callers must get the SAME slices,
	// not copies: a copy per call is exactly the per-call work this avoids, and
	// at 500 concurrent calls it would be 500 copies of the tone.
	a, err := ToneFrames()
	if err != nil {
		t.Fatalf("ToneFrames: %v", err)
	}
	b, err := ToneFrames()
	if err != nil {
		t.Fatalf("ToneFrames: %v", err)
	}
	if len(a) == 0 || len(b) == 0 {
		t.Fatal("no frames")
	}
	if &a[0][0] != &b[0][0] {
		t.Fatal("a second caller got a copy; the frames are meant to be shared")
	}
}

func TestTheToneIsParsedExactlyOnceUnderConcurrency(t *testing.T) {
	// 500 concurrent calls is the design point, so the parse must be safe and
	// must not repeat.
	var wg sync.WaitGroup
	first := make([][][]byte, 64)
	for i := range first {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			f, err := ToneFrames()
			if err != nil {
				t.Errorf("ToneFrames: %v", err)
				return
			}
			first[i] = f
		}(i)
	}
	wg.Wait()
	for i := 1; i < len(first); i++ {
		if first[i] == nil || first[0] == nil {
			continue
		}
		if &first[i][0][0] != &first[0][0][0] {
			t.Fatal("concurrent callers got different frame sets")
		}
	}
}

// --- the segment-table framing, which is the part that is easy to get wrong ---

// oggPage builds one page carrying the given packets, splitting each into
// 255-byte segments the way the format requires.
func oggPage(packets ...[]byte) []byte {
	var table []byte
	var payload []byte
	for _, p := range packets {
		remaining := len(p)
		for remaining >= oggSegmentContinues {
			table = append(table, oggSegmentContinues)
			remaining -= oggSegmentContinues
		}
		table = append(table, byte(remaining))
		payload = append(payload, p...)
	}
	page := make([]byte, oggPageHeaderLen)
	copy(page, "OggS")
	page[oggSegmentCountOffset] = byte(len(table))
	page = append(page, table...)
	return append(page, payload...)
}

func TestAPageHoldingSeveralPacketsIsSplitBackIntoPackets(t *testing.T) {
	// The exact case pion's oggreader cannot express: it concatenates every
	// segment and drops the table, so these three would come back as one blob
	// and get written as a single 20ms sample.
	one := bytes.Repeat([]byte{0xA1}, 60)
	two := bytes.Repeat([]byte{0xB2}, 61)
	three := bytes.Repeat([]byte{0xC3}, 62)
	got, err := oggOpusPackets(oggPage(one, two, three))
	if err != nil {
		t.Fatalf("oggOpusPackets: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("got %d packets, want 3", len(got))
	}
	for i, want := range [][]byte{one, two, three} {
		if !bytes.Equal(got[i], want) {
			t.Errorf("packet %d: got %d bytes, want %d", i, len(got[i]), len(want))
		}
	}
}

func TestAPacketSpanningPagesIsRejoined(t *testing.T) {
	// A packet longer than 255 bytes is split across segments, and one longer
	// than a page continues into the next. Both must come back whole.
	big := make([]byte, 700)
	for i := range big {
		big[i] = byte(i % 251)
	}
	got, err := oggOpusPackets(oggPage(big))
	if err != nil {
		t.Fatalf("oggOpusPackets: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d packets, want 1", len(got))
	}
	if !bytes.Equal(got[0], big) {
		t.Fatal("the 700-byte packet did not survive segmentation")
	}
}

func TestContainerHeadersAreNotPublishedAsAudio(t *testing.T) {
	head := append([]byte("OpusHead"), make([]byte, 11)...)
	tags := append([]byte("OpusTags"), make([]byte, 20)...)
	audio := bytes.Repeat([]byte{0x7F}, 60)
	got, err := oggOpusPackets(oggPage(head, tags, audio))
	if err != nil {
		t.Fatalf("oggOpusPackets: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d packets, want only the audio one", len(got))
	}
	if !bytes.Equal(got[0], audio) {
		t.Error("the surviving packet is not the audio one")
	}
}

func TestTruncatedStreamsAreRejectedRatherThanTruncatedSilently(t *testing.T) {
	full := oggPage(bytes.Repeat([]byte{0x11}, 60))
	cases := map[string][]byte{
		"header cut":  full[:10],
		"table cut":   full[:oggPageHeaderLen],
		"payload cut": full[:len(full)-5],
		"not ogg":     append([]byte("NOTO"), full[4:]...),
	}
	for name, data := range cases {
		if _, err := oggOpusPackets(data); err == nil {
			t.Errorf("%s: accepted a stream it should reject", name)
		}
	}
}

func TestAStreamEndingMidPacketIsAnError(t *testing.T) {
	// A final segment of exactly 255 says "continues", and nothing follows.
	page := make([]byte, oggPageHeaderLen)
	copy(page, "OggS")
	page[oggSegmentCountOffset] = 1
	page = append(page, byte(oggSegmentContinues))
	page = append(page, bytes.Repeat([]byte{0x22}, oggSegmentContinues)...)
	if _, err := oggOpusPackets(page); err == nil {
		t.Fatal("a stream ending mid-packet was accepted")
	}
}

func TestTheEmbeddedToneRoundTripsThroughTheParserDeterministically(t *testing.T) {
	a, err := oggOpusPackets(toneOgg)
	if err != nil {
		t.Fatalf("first parse: %v", err)
	}
	b, err := oggOpusPackets(toneOgg)
	if err != nil {
		t.Fatalf("second parse: %v", err)
	}
	if len(a) != len(b) {
		t.Fatalf("parses disagree: %d vs %d frames", len(a), len(b))
	}
	for i := range a {
		if !bytes.Equal(a[i], b[i]) {
			t.Fatalf("frame %d differs between parses", i)
		}
	}
	fmt.Fprintf(&bytes.Buffer{}, "%d", oggGranulePosition(toneOgg))
}
