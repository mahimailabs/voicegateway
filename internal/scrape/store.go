package scrape

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"slices"
	"time"

	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/redis/go-redis/v9"
	"github.com/twitchtv/twirp"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Projects lists the projects to read and opens their LiveKit secrets.
type Projects interface {
	Projects(ctx context.Context) ([]store.Project, error)
	LiveKitSecret(ctx context.Context, organisationID, projectID string) (string, error)
}

// Samples stores a read and what it saw.
type Samples interface {
	StoreRead(ctx context.Context, read store.LayerRead, rooms []store.RoomSample, participants []store.ParticipantSample) error
	RoomsWithWebhooks(ctx context.Context, organisationID, projectID string, since time.Time) ([]string, error)
}

// SIPSamples stores a SIP read and, when it succeeded, what it saw.
type SIPSamples interface {
	StoreSIPRead(ctx context.Context, read store.LayerRead, sip *store.SIP) error
}

// EgressSamples stores an egress read and, when it succeeded, what it saw.
type EgressSamples interface {
	StoreEgressRead(ctx context.Context, read store.LayerRead, egress []store.EgressSample) error
}

// IngressSamples stores an ingress read and, when it succeeded, what it saw.
type IngressSamples interface {
	StoreIngressRead(ctx context.Context, read store.LayerRead, ingress []store.IngressSample) error
}

// CloudAgentSamples stores a Cloud agents read and, when it succeeded, what it saw.
type CloudAgentSamples interface {
	StoreCloudAgentsRead(ctx context.Context, read store.LayerRead, agents *store.CloudAgents) error
}

// WorkerEndpoints lists a project's self-hosted worker metrics endpoints with their URLs opened.
type WorkerEndpoints interface {
	OpenWorkerEndpoints(ctx context.Context, organisationID, projectID string) ([]store.OpenWorkerEndpoint, error)
}

// WorkerSamples stores a read of a project's worker metrics endpoints and what they reported.
type WorkerSamples interface {
	StoreWorkerRead(ctx context.Context, read store.LayerRead, endpoints []store.WorkerEndpointRead, samples []store.WorkerSample) error
}

// Locker takes a lock for ttl if nobody holds it, so one worker reads a layer per interval, and
// swaps a value shared by the workers, returning the one before.
type Locker interface {
	Lock(ctx context.Context, key string, ttl time.Duration) (bool, error)
	Swap(ctx context.Context, key, value string, ttl time.Duration) (string, error)
}

// LiveKit is the part of a LiveKit server's API the layer reads use.
type LiveKit interface {
	ListRooms(ctx context.Context) ([]*livekit.Room, error)
	ListParticipants(ctx context.Context, room string) ([]*livekit.ParticipantInfo, error)
	ListDispatch(ctx context.Context, room string) ([]*livekit.AgentDispatch, error)
	ListSIPInboundTrunk(ctx context.Context) ([]*livekit.SIPInboundTrunkInfo, error)
	ListSIPOutboundTrunk(ctx context.Context) ([]*livekit.SIPOutboundTrunkInfo, error)
	ListSIPDispatchRule(ctx context.Context) ([]*livekit.SIPDispatchRuleInfo, error)
	ListPhoneNumbers(ctx context.Context) ([]*livekit.PhoneNumber, error)
	ListEgress(ctx context.Context) ([]*livekit.EgressInfo, error)
	ListIngress(ctx context.Context) ([]*livekit.IngressInfo, error)
	ListAgents(ctx context.Context) ([]*livekit.AgentInfo, error)
	ListAgentVersions(ctx context.Context, agentID string) ([]*livekit.AgentVersion, error)
}

// Dial returns a LiveKit client for one project.
type Dial func(url, apiKey, apiSecret string) LiveKit

// DialSDK reads LiveKit through its Go server SDK's own clients, which dial with the SDK's HTTP
// client over http.DefaultTransport and fail over between LiveKit Cloud regions. No address guard
// applies; NewDial(true) gives the guarded client instead.
func DialSDK(url, apiKey, apiSecret string) LiveKit {
	return sdkClient{
		rooms:      lksdk.NewRoomServiceClient(url, apiKey, apiSecret),
		dispatches: lksdk.NewAgentDispatchServiceClient(url, apiKey, apiSecret),
		sip:        lksdk.NewSIPClient(url, apiKey, apiSecret),
		numbers:    lksdk.NewPhoneNumberClient(url, apiKey, apiSecret),
		egress:     lksdk.NewEgressClient(url, apiKey, apiSecret),
		ingress:    lksdk.NewIngressClient(url, apiKey, apiSecret),
		url:        url, apiKey: apiKey, apiSecret: apiSecret,
	}
}

// The LiveKit API calls a read makes, as the SDK's clients and the guarded ones in livekit.go both
// offer them.
type (
	roomCalls interface {
		ListRooms(ctx context.Context, req *livekit.ListRoomsRequest) (*livekit.ListRoomsResponse, error)
		ListParticipants(ctx context.Context, req *livekit.ListParticipantsRequest) (*livekit.ListParticipantsResponse, error)
	}
	dispatchCalls interface {
		ListDispatch(ctx context.Context, req *livekit.ListAgentDispatchRequest) (*livekit.ListAgentDispatchResponse, error)
	}
	sipCalls interface {
		ListSIPInboundTrunk(ctx context.Context, req *livekit.ListSIPInboundTrunkRequest) (*livekit.ListSIPInboundTrunkResponse, error)
		ListSIPOutboundTrunk(ctx context.Context, req *livekit.ListSIPOutboundTrunkRequest) (*livekit.ListSIPOutboundTrunkResponse, error)
		ListSIPDispatchRule(ctx context.Context, req *livekit.ListSIPDispatchRuleRequest) (*livekit.ListSIPDispatchRuleResponse, error)
	}
	numberCalls interface {
		ListPhoneNumbers(ctx context.Context, req *livekit.ListPhoneNumbersRequest) (*livekit.ListPhoneNumbersResponse, error)
	}
	egressCalls interface {
		ListEgress(ctx context.Context, req *livekit.ListEgressRequest) (*livekit.ListEgressResponse, error)
	}
	ingressCalls interface {
		ListIngress(ctx context.Context, req *livekit.ListIngressRequest) (*livekit.ListIngressResponse, error)
	}
)

type sdkClient struct {
	rooms      roomCalls
	dispatches dispatchCalls
	sip        sipCalls
	numbers    numberCalls
	egress     egressCalls
	ingress    ingressCalls
	// The Cloud agents client is made only when a Cloud agents read runs, so a read of any other
	// layer never resolves the Cloud agents endpoint. agentOptions is empty for the SDK's own HTTP
	// client, or names the guarded one.
	url, apiKey, apiSecret string
	agentOptions           []lksdk.AgentClientOption
}

func (c sdkClient) ListRooms(ctx context.Context) ([]*livekit.Room, error) {
	res, err := c.rooms.ListRooms(ctx, &livekit.ListRoomsRequest{})
	return res.GetRooms(), err
}

func (c sdkClient) ListParticipants(ctx context.Context, room string) ([]*livekit.ParticipantInfo, error) {
	res, err := c.rooms.ListParticipants(ctx, &livekit.ListParticipantsRequest{Room: room})
	return res.GetParticipants(), err
}

func (c sdkClient) ListDispatch(ctx context.Context, room string) ([]*livekit.AgentDispatch, error) {
	res, err := c.dispatches.ListDispatch(ctx, &livekit.ListAgentDispatchRequest{Room: room})
	return res.GetAgentDispatches(), err
}

// The SIP lists are read without a page, which LiveKit answers with every item.

func (c sdkClient) ListSIPInboundTrunk(ctx context.Context) ([]*livekit.SIPInboundTrunkInfo, error) {
	res, err := c.sip.ListSIPInboundTrunk(ctx, &livekit.ListSIPInboundTrunkRequest{})
	return res.GetItems(), err
}

func (c sdkClient) ListSIPOutboundTrunk(ctx context.Context) ([]*livekit.SIPOutboundTrunkInfo, error) {
	res, err := c.sip.ListSIPOutboundTrunk(ctx, &livekit.ListSIPOutboundTrunkRequest{})
	return res.GetItems(), err
}

func (c sdkClient) ListSIPDispatchRule(ctx context.Context) ([]*livekit.SIPDispatchRuleInfo, error) {
	res, err := c.sip.ListSIPDispatchRule(ctx, &livekit.ListSIPDispatchRuleRequest{})
	return res.GetItems(), err
}

// maxNumberPages bounds the phone number pages one read follows, at LiveKit's default 50 a page.
const maxNumberPages = 100

func (c sdkClient) ListPhoneNumbers(ctx context.Context) ([]*livekit.PhoneNumber, error) {
	var out []*livekit.PhoneNumber
	req := &livekit.ListPhoneNumbersRequest{}
	for range maxNumberPages {
		res, err := c.numbers.ListPhoneNumbers(ctx, req)
		if err != nil {
			return nil, err
		}
		out = append(out, res.GetItems()...)
		next := res.GetNextPageToken()
		if next.GetToken() == "" {
			return out, nil
		}
		req.PageToken = next
	}
	return nil, fmt.Errorf("phone numbers: more than %d pages", maxNumberPages)
}

// maxListPages bounds the egress and ingress pages one read follows. LiveKit answers the first page
// with every item today; the page token is followed in case a server pages.
const maxListPages = 100

func (c sdkClient) ListEgress(ctx context.Context) ([]*livekit.EgressInfo, error) {
	var out []*livekit.EgressInfo
	req := &livekit.ListEgressRequest{}
	for range maxListPages {
		res, err := c.egress.ListEgress(ctx, req)
		if err != nil {
			return nil, err
		}
		out = append(out, res.GetItems()...)
		next := res.GetNextPageToken()
		if next.GetToken() == "" {
			return out, nil
		}
		req.PageToken = next
	}
	return nil, fmt.Errorf("egress: more than %d pages", maxListPages)
}

func (c sdkClient) ListIngress(ctx context.Context) ([]*livekit.IngressInfo, error) {
	var out []*livekit.IngressInfo
	req := &livekit.ListIngressRequest{}
	for range maxListPages {
		res, err := c.ingress.ListIngress(ctx, req)
		if err != nil {
			return nil, err
		}
		out = append(out, res.GetItems()...)
		next := res.GetNextPageToken()
		if next.GetToken() == "" {
			return out, nil
		}
		req.PageToken = next
	}
	return nil, fmt.Errorf("ingress: more than %d pages", maxListPages)
}

// cliVersionHeader is the header LiveKit Cloud's agents API requires on every call: without it,
// ListAgents answers Twirp "malformed: livekit-cli version header is required" (seen on a Cloud
// project, 2026-10-29). server-sdk-go v2.19.0's AgentClient does not send it; its pkg/cloudagents
// client, which lk uses, sets it to lksdk.Version from a RequestPrepared hook (auth.go
// setLivekitHeaders). That package pulls in BuildKit for deploys, so the same hook is set here.
const cliVersionHeader = "X-LIVEKIT-CLI-VERSION"

// withCLIVersion sends cliVersionHeader as pkg/cloudagents does.
var withCLIVersion = lksdk.WithTwirpClientOptions(twirp.WithClientHooks(&twirp.ClientHooks{
	RequestPrepared: func(ctx context.Context, req *http.Request) (context.Context, error) {
		req.Header.Set(cliVersionHeader, lksdk.Version)
		return ctx, nil
	},
}))

// agentClient makes the Cloud agents client. NewAgentClient never fails in server-sdk-go v2.19.0;
// its error is still returned in case a later version does.
func (c sdkClient) agentClient() (*lksdk.AgentClient, error) {
	agents, err := lksdk.NewAgentClient(c.url, c.apiKey, c.apiSecret, append(slices.Clip(c.agentOptions), withCLIVersion)...)
	if err != nil {
		return nil, fmt.Errorf("cloud agents client: %w", err)
	}
	return agents, nil
}

// ListAgents lists the project's LiveKit Cloud agents.
func (c sdkClient) ListAgents(ctx context.Context) ([]*livekit.AgentInfo, error) {
	agents, err := c.agentClient()
	if err != nil {
		return nil, err
	}
	res, err := agents.ListAgents(ctx, &livekit.ListAgentsRequest{})
	return res.GetAgents(), err
}

// ListAgentVersions lists one Cloud agent's versions.
func (c sdkClient) ListAgentVersions(ctx context.Context, agentID string) ([]*livekit.AgentVersion, error) {
	agents, err := c.agentClient()
	if err != nil {
		return nil, err
	}
	res, err := agents.ListAgentVersions(ctx, &livekit.ListAgentVersionsRequest{AgentId: agentID})
	return res.GetVersions(), err
}

// RedisLock is a Locker over Redis SET NX with an expiry.
type RedisLock struct{ Client *redis.Client }

// Lock takes key for ttl if nobody holds it.
func (l RedisLock) Lock(ctx context.Context, key string, ttl time.Duration) (bool, error) {
	ok, err := l.Client.SetNX(ctx, key, "1", ttl).Result()
	if err != nil {
		return false, fmt.Errorf("redis lock: %w", err)
	}
	return ok, nil
}

// Swap sets key to value for ttl and returns what it held, empty if nothing.
func (l RedisLock) Swap(ctx context.Context, key, value string, ttl time.Duration) (string, error) {
	prev, err := l.Client.SetArgs(ctx, key, value, redis.SetArgs{TTL: ttl, Get: true}).Result()
	if errors.Is(err, redis.Nil) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("redis swap: %w", err)
	}
	return prev, nil
}
