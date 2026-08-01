package main

import (
	_ "embed"
	"encoding/binary"
	"errors"
	"fmt"
	"sync"
	"time"
)

// tone.ogg is one second of 440Hz sine, encoded as Opus at 48kHz mono in 20ms
// frames. It is embedded rather than read from disk so the binary that runs on a
// load generator carries its own audio and cannot be started against a missing
// file mid-run.
//
//go:embed tone.ogg
var toneOgg []byte

// ToneFrameDuration is the Opus frame size tone.ogg was encoded at. Every frame
// in the parsed set represents exactly this much audio, which is what lets the
// sender pace on a fixed ticker instead of reading a per-frame duration.
const ToneFrameDuration = 20 * time.Millisecond

var (
	toneOnce   sync.Once
	toneFrames [][]byte
	toneErr    error
)

// ToneFrames returns the shared Opus frames, parsed exactly once.
//
// THIS IS THE POINT OF THE FILE. livekit-sip does not send 200 OK until it has
// subscribed to an audio track, so everything this worker does before audio
// flows is SIP answer latency. Encoding per call would put an Opus encoder on
// that path at 500 concurrent calls; parsing per call would put an Ogg parser
// there. Both are done once, at startup, and every track then shares the same
// backing arrays.
//
// The returned slices are READ ONLY. They are shared across every concurrent
// call, so a caller that mutates one corrupts audio for all of them. Nothing
// here copies, deliberately: a copy per call is exactly the per-call work this
// exists to avoid.
func ToneFrames() ([][]byte, error) {
	toneOnce.Do(func() {
		toneFrames, toneErr = oggOpusPackets(toneOgg)
		if toneErr == nil && len(toneFrames) == 0 {
			toneErr = errors.New("tone.ogg carried no Opus packets")
		}
	})
	return toneFrames, toneErr
}

const (
	oggPageHeaderLen = 27
	// Offset of the segment count within the page header.
	oggSegmentCountOffset = 26
	// A segment of exactly 255 bytes means the packet continues into the next
	// segment. Anything shorter terminates it. This is the whole of Ogg's
	// packet framing.
	oggSegmentContinues = 255
)

// oggOpusPackets reconstructs Opus packets from an Ogg Opus stream.
//
// It walks the SEGMENT TABLE rather than taking each page's payload whole. A
// page can carry several packets, and pion's oggreader concatenates every
// segment into one buffer and drops the table, so a page holding three 20ms
// packets comes back as one 60ms blob with no way to split it. Writing that as a
// single 20ms sample sends malformed audio that a receiver either drops or
// renders as a glitch, and neither failure names its cause.
//
// Doing the framing here also means the tone can be re-encoded with any muxer
// settings without silently changing what a "frame" is.
//
// The two Opus header packets (OpusHead and OpusTags) are dropped: they are
// container metadata, not audio, and publishing them as samples would put four
// bytes of ASCII where a decoder expects a frame.
func oggOpusPackets(data []byte) ([][]byte, error) {
	var packets [][]byte
	var partial []byte
	offset := 0
	for offset < len(data) {
		if offset+oggPageHeaderLen > len(data) {
			return nil, fmt.Errorf("truncated ogg page header at byte %d", offset)
		}
		header := data[offset : offset+oggPageHeaderLen]
		if string(header[0:4]) != "OggS" {
			return nil, fmt.Errorf("not an ogg page at byte %d", offset)
		}
		segmentCount := int(header[oggSegmentCountOffset])
		tableStart := offset + oggPageHeaderLen
		if tableStart+segmentCount > len(data) {
			return nil, fmt.Errorf("truncated ogg segment table at byte %d", tableStart)
		}
		table := data[tableStart : tableStart+segmentCount]

		payload := tableStart + segmentCount
		for _, size := range table {
			end := payload + int(size)
			if end > len(data) {
				return nil, fmt.Errorf("truncated ogg segment at byte %d", payload)
			}
			partial = append(partial, data[payload:end]...)
			payload = end
			if size < oggSegmentContinues {
				// Segment shorter than 255 ends the packet.
				packets = append(packets, partial)
				partial = nil
			}
		}
		offset = payload
	}
	if len(partial) > 0 {
		return nil, errors.New("ogg stream ended mid-packet")
	}
	return dropOpusHeaders(packets), nil
}

// dropOpusHeaders removes the OpusHead and OpusTags packets.
func dropOpusHeaders(packets [][]byte) [][]byte {
	out := make([][]byte, 0, len(packets))
	for _, p := range packets {
		if isOpusHeader(p) {
			continue
		}
		out = append(out, p)
	}
	return out
}

func isOpusHeader(packet []byte) bool {
	if len(packet) >= 8 && string(packet[0:8]) == "OpusHead" {
		return true
	}
	return len(packet) >= 8 && string(packet[0:8]) == "OpusTags"
}

// oggGranulePosition reads a page's granule position, used only by the tests to
// check the embedded tone is as long as it claims.
func oggGranulePosition(page []byte) uint64 {
	if len(page) < oggPageHeaderLen {
		return 0
	}
	return binary.LittleEndian.Uint64(page[6:14])
}
