package sink

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/url"
	"regexp"
	"slices"
	"strings"

	"github.com/livekit/protocol/livekit"
	lklogger "github.com/livekit/protocol/livekit/logger"
	"github.com/livekit/protocol/utils"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
)

// errWebhookUndecodable says only that an Egress or Ingress webhook body is not a WebhookEvent:
// protojson's own error can quote a token of the body, and the reason is logged and dead-lettered.
var errWebhookUndecodable = errors.New("webhook body: not a LiveKit WebhookEvent")

// carriesEgressOrIngress reports whether a webhook body may be an Egress or Ingress one: its event
// name or its info member, in protojson's camelCase or the proto name. Room, participant, track and
// job webhooks never contain either.
func carriesEgressOrIngress(body []byte) bool {
	return bytes.Contains(body, []byte(`"egress`)) || bytes.Contains(body, []byte(`"ingress`))
}

// withoutWebhookSecrets removes from a LiveKit webhook body what VoiceGateway must not store: the
// room's TURN password and, for Egress and Ingress, every credential, stream key and personal
// field (fix/webhook-egress-ingress-secrets). Other bodies are returned as they came.
func withoutWebhookSecrets(body []byte) ([]byte, error) {
	if !carriesEgressOrIngress(body) {
		return withoutTURNPassword(body)
	}
	var ev livekit.WebhookEvent
	// DiscardUnknown drops fields newer than livekit/protocol v1.52.1 too: a field this code has
	// not reviewed is not stored.
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(body, &ev); err != nil {
		return nil, errWebhookUndecodable
	}
	if ev.GetEgressInfo() == nil && ev.GetIngressInfo() == nil {
		return withoutTURNPassword(body)
	}
	if ev.Room != nil {
		ev.Room.TurnPassword = ""
	}
	redactEgress(ev.GetEgressInfo())
	redactIngress(ev.GetIngressInfo())
	out, err := protojson.Marshal(&ev)
	if err != nil {
		return nil, errWebhookUndecodable
	}
	// protojson varies its whitespace on purpose; the stored body is compact, as LiveKit's is.
	var compact bytes.Buffer
	if err := json.Compact(&compact, out); err != nil {
		return nil, errWebhookUndecodable
	}
	return compact.Bytes(), nil
}

// redactEgress keeps an Egress's ids, room, status, timings, errors and where its results went, and
// drops its start request. The request holds the storage credentials (S3, GCP, Azure, AliOSS),
// proxy credentials, webhook signing keys, stream URLs with their keys and the page URL of a web
// egress. livekit/protocol's egress.RedactStartEgressRequest and RedactEncodedOutputs cover only the
// storage credentials and stream keys, and keep up to six characters of each key, so the request
// is not kept at all; nothing in VoiceGateway reads it. Error and detail text loses every value
// dropped here, as internal/scrape cuts it for the console (phase 05c).
func redactEgress(info *livekit.EgressInfo) {
	if info == nil {
		return
	}
	secrets := egressSecrets(info)
	info.Request = nil
	info.Error = maskPhoneNumbers(scrubText(info.GetError(), secrets))
	info.Details = maskPhoneNumbers(scrubText(info.GetDetails(), secrets))
	streams := slices.Clone(info.GetStreamResults())
	// Older servers report results only in the deprecated fields, which hold the same secrets.
	streams = append(streams, info.GetStream().GetInfo()...) //nolint:staticcheck // older servers set only the deprecated field
	for _, s := range streams {
		s.Url = streamDestination(s.GetUrl())
		s.Error = maskPhoneNumbers(scrubText(s.GetError(), secrets))
	}
	files := slices.Clone(info.GetFileResults())
	if f := info.GetFile(); f != nil { //nolint:staticcheck // older servers set only the deprecated field
		files = append(files, f)
	}
	for _, f := range files {
		f.Location = utils.RedactUrlQueryValues(f.GetLocation())
	}
	segments := slices.Clone(info.GetSegmentResults())
	if s := info.GetSegments(); s != nil { //nolint:staticcheck // older servers set only the deprecated field
		segments = append(segments, s)
	}
	for _, s := range segments {
		s.PlaylistLocation = utils.RedactUrlQueryValues(s.GetPlaylistLocation())
		s.LivePlaylistLocation = utils.RedactUrlQueryValues(s.GetLivePlaylistLocation())
	}
	info.ManifestLocation = utils.RedactUrlQueryValues(info.GetManifestLocation())
}

// redactIngress drops an Ingress's stream key, participant name and metadata, keeps only the
// scheme and host of its URL (a WHIP or pull URL carries a token in its path or query) and masks
// phone numbers in its participant identity, track names and error text, which also loses every
// value dropped here. livekit/protocol has no Ingress redaction helper.
func redactIngress(info *livekit.IngressInfo) {
	if info == nil {
		return
	}
	secrets := append([]string{info.GetStreamKey(), info.GetParticipantName(), info.GetParticipantMetadata()}, urlParts(info.GetUrl())...)
	info.StreamKey = ""
	info.Url = streamDestination(info.GetUrl())
	info.ParticipantName = ""
	info.ParticipantMetadata = ""
	info.ParticipantIdentity = maskPhoneNumbers(info.GetParticipantIdentity())
	if st := info.GetState(); st != nil {
		st.Error = maskPhoneNumbers(scrubText(st.GetError(), secrets))
		for _, t := range st.GetTracks() {
			t.Name = maskPhoneNumbers(t.GetName())
		}
	}
}

// streamDestination keeps only where a stream goes, its scheme and host, never its user, path or
// query, where stream keys and tokens live. mux:// and twitch:// URLs carry the key as their host,
// so only their scheme is kept. A URL with no host keeps nothing. livekit/protocol's
// utils.RedactStreamKey keeps part of the key.
func streamDestination(raw string) string {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme == "" {
		return ""
	}
	scheme := strings.ToLower(u.Scheme)
	if scheme == "mux" || scheme == "twitch" {
		return scheme + "://"
	}
	if u.Host == "" {
		return ""
	}
	return scheme + "://" + u.Host
}

// anyURL matches a URL inside free text, such as an error naming the stream it failed to reach.
var anyURL = regexp.MustCompile(`[A-Za-z][A-Za-z0-9+.-]*://[^\s"'<>,;]+`)

// redacted replaces a dropped value that LiveKit repeats in text.
const redacted = "[redacted]"

// minSecret is the shortest dropped value cut from text; shorter ones would cut ordinary words.
const minSecret = 4

// scrubText cuts every URL in text down to its scheme and host and every dropped value out of it,
// longest first so a value inside another is cut with it. It repeats internal/scrape's scrub.
func scrubText(text string, secrets []string) string {
	if text == "" {
		return ""
	}
	text = anyURL.ReplaceAllStringFunc(text, func(u string) string {
		if d := streamDestination(u); d != "" {
			return d
		}
		return redacted
	})
	secrets = slices.Clone(secrets)
	slices.SortFunc(secrets, func(a, b string) int { return len(b) - len(a) })
	for _, s := range secrets {
		if len(s) >= minSecret {
			text = strings.ReplaceAll(text, s, redacted)
		}
	}
	return text
}

// egressSecrets collects every value redactEgress drops: each request field LiveKit marks secret or
// personal (logger.sensitivity), the URLs, keys and credentials it leaves unmarked, the stream
// result URLs and the query values of result locations. URLs come with their parts.
func egressSecrets(info *livekit.EgressInfo) []string {
	var out []string
	var walk func(m protoreflect.Message)
	walk = func(m protoreflect.Message) {
		m.Range(func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
			// A map field reports the kind of its values, so maps are told apart first.
			switch {
			case fd.IsMap():
				values := fd.MapValue()
				v.Map().Range(func(_ protoreflect.MapKey, mv protoreflect.Value) bool {
					switch {
					case values.Kind() == protoreflect.MessageKind:
						walk(mv.Message())
					case values.Kind() == protoreflect.StringKind && secretField(fd):
						out = append(out, urlParts(mv.String())...)
					}
					return true
				})
			case fd.Kind() == protoreflect.MessageKind && fd.IsList():
				for i := range v.List().Len() {
					walk(v.List().Get(i).Message())
				}
			case fd.Kind() == protoreflect.MessageKind:
				walk(v.Message())
			case fd.Kind() == protoreflect.StringKind && secretField(fd):
				if fd.IsList() {
					for i := range v.List().Len() {
						out = append(out, urlParts(v.List().Get(i).String())...)
					}
				} else {
					out = append(out, urlParts(v.String())...)
				}
			}
			return true
		})
	}
	// The request is the one populated member of EgressInfo's request oneof.
	m := info.ProtoReflect()
	if fd := m.WhichOneof(m.Descriptor().Oneofs().ByName("request")); fd != nil {
		walk(m.Get(fd).Message())
	}
	for _, s := range info.GetStreamResults() {
		out = append(out, urlParts(s.GetUrl())...)
	}
	for _, s := range info.GetStream().GetInfo() { //nolint:staticcheck // older servers set only the deprecated field
		out = append(out, urlParts(s.GetUrl())...)
	}
	locations := []string{info.GetManifestLocation()}
	for _, f := range info.GetFileResults() {
		locations = append(locations, f.GetLocation())
	}
	for _, s := range info.GetSegmentResults() {
		locations = append(locations, s.GetPlaylistLocation(), s.GetLivePlaylistLocation())
	}
	for _, l := range locations {
		if u, err := url.Parse(l); err == nil {
			for _, vs := range u.Query() {
				out = append(out, vs...)
			}
		}
	}
	return out
}

// urlParts is a dropped value and, when it is a URL, each part of it that can carry a key on its
// own: the user and password, every path segment, every query value, and the host of a shorthand
// URL such as mux://<key>, so a part LiveKit repeats without the rest of the URL is cut too.
func urlParts(v string) []string {
	out := []string{v}
	u, err := url.Parse(strings.TrimSpace(strings.TrimSuffix(v, " live=1")))
	if err != nil || u.Scheme == "" {
		return out
	}
	if s := strings.ToLower(u.Scheme); s == "mux" || s == "twitch" {
		out = append(out, u.Host)
	}
	if u.User != nil {
		out = append(out, u.User.Username())
		if p, ok := u.User.Password(); ok {
			out = append(out, p)
		}
	}
	out = append(out, strings.Split(u.Path, "/")...)
	for _, vs := range u.Query() {
		out = append(out, vs...)
	}
	return out
}

// unmarkedSecrets are request fields that carry a URL, key or credential without LiveKit's
// sensitivity mark in protocol v1.52.1.
var unmarkedSecrets = map[protoreflect.Name]bool{
	"url": true, "urls": true, "websocket_url": true, "signing_key": true,
	"username": true, "password": true, "custom_base_url": true,
}

func secretField(fd protoreflect.FieldDescriptor) bool {
	if unmarkedSecrets[fd.Name()] {
		return true
	}
	if !proto.HasExtension(fd.Options(), lklogger.E_Sensitivity) {
		return false
	}
	s, ok := proto.GetExtension(fd.Options(), lklogger.E_Sensitivity).(lklogger.Sensitivity)
	return ok && s != lklogger.Sensitivity_SENSITIVITY_UNSPECIFIED
}

// deadLetterWebhook scrubs a webhook body for the dead-letter stream. A body that decodes is
// stripped as a stored one would be; an Egress or Ingress body that does not is withheld, since
// its secrets cannot be found reliably, and its entry keeps the reason it failed.
func deadLetterWebhook(body []byte) []byte {
	if out, err := withoutWebhookSecrets(body); err == nil {
		return out
	}
	if carriesEgressOrIngress(body) {
		return nil
	}
	return blankTURNPassword(body)
}

// phoneDigits matches a run of digits long enough to be a phone number. It and maskPhoneNumbers
// repeat internal/scrape's, which sink may not import (CLAUDE.md, Boundaries).
var phoneDigits = regexp.MustCompile(`\+?\d{7,}`)

// maskPhoneNumbers masks any phone number in a name to its last four digits: sip_•••• 4567.
func maskPhoneNumbers(name string) string {
	return phoneDigits.ReplaceAllStringFunc(name, func(n string) string {
		digits := make([]rune, 0, len(n))
		for _, r := range n {
			if r >= '0' && r <= '9' {
				digits = append(digits, r)
			}
		}
		return "•••• " + string(digits[len(digits)-4:])
	})
}
