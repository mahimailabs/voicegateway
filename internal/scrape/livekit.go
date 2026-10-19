package scrape

import (
	"context"
	"fmt"
	"net/http"

	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/utils/xtwirp"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/livekit/server-sdk-go/v2/signalling"
	"github.com/twitchtv/twirp"
)

// NewDial returns how the worker reaches each project's LiveKit API. Without publicOnly it is
// DialSDK, unchanged. With publicOnly (VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY, for an install whose
// projects are added by people it should not trust with its network, such as Cloud) every call goes
// through NewWorkerClient(true): code.dny.dev/ssrf checks each address after DNS and refuses private,
// loopback, link-local and metadata ones, redirects are not followed and proxy settings are ignored.
//
// The pinned server-sdk-go v2.19.0 builds its room, dispatch, SIP, phone number, egress and
// ingress clients over its own http.Client (newAPIHTTPClient in failover.go) and offers no option to
// pass another; only its AgentClient takes WithHTTPClient. So the guarded dial builds those Twirp
// clients from livekit/protocol's generated constructors with the guarded client, and signs each call
// with the grant the SDK's own method uses (auth.AccessToken). What it gives up is the SDK's LiveKit
// Cloud region failover, which would retry against hosts the guard has not seen.
func NewDial(publicOnly bool) Dial {
	if !publicOnly {
		return DialSDK
	}
	return DialThrough(NewWorkerClient(true))
}

// DialThrough reads LiveKit over Twirp clients that dial through client, and the SDK's AgentClient
// given client with WithHTTPClient.
func DialThrough(client *http.Client) Dial {
	return func(url, apiKey, apiSecret string) LiveKit {
		return clientThrough(client, url, apiKey, apiSecret)
	}
}

func clientThrough(client *http.Client, url, apiKey, apiSecret string) LiveKit {
	base := signalling.ToHttpURL(url)
	opts := xtwirp.DefaultClientOptions()
	s := signer{apiKey: apiKey, apiSecret: apiSecret}
	return sdkClient{
		rooms:      signedRooms{livekit.NewRoomServiceProtobufClient(base, client, opts...), s},
		dispatches: signedDispatches{livekit.NewAgentDispatchServiceProtobufClient(base, client, opts...), s},
		sip:        signedSIP{livekit.NewSIPProtobufClient(base, client, opts...), s},
		numbers:    signedNumbers{livekit.NewPhoneNumberServiceProtobufClient(base, client, opts...), s},
		egress:     signedEgress{livekit.NewEgressProtobufClient(base, client, opts...), s},
		ingress:    signedIngress{livekit.NewIngressProtobufClient(base, client, opts...), s},
		url:        url, apiKey: apiKey, apiSecret: apiSecret,
		agentOptions: []lksdk.AgentClientOption{lksdk.WithHTTPClient(client)},
	}
}

// signer puts on a call's context the bearer token the SDK's own client would send: an access token
// for the project's key, holding the one grant the call needs (server-sdk-go v2.19.0 auth.go
// prepareContext, minus its request id and failover deadline, which serve its retries).
type signer struct{ apiKey, apiSecret string }

func (s signer) with(ctx context.Context, grant func(*auth.AccessToken)) (context.Context, error) {
	at := auth.NewAccessToken(s.apiKey, s.apiSecret)
	grant(at)
	token, err := at.ToJWT()
	if err != nil {
		return nil, fmt.Errorf("sign LiveKit request: %w", err)
	}
	return twirp.WithHTTPRequestHeaders(ctx, signalling.NewHTTPHeaderWithToken(token))
}

func video(g auth.VideoGrant) func(*auth.AccessToken) {
	return func(at *auth.AccessToken) { at.SetVideoGrant(&g) }
}

func sipAdmin(at *auth.AccessToken) { at.SetSIPGrant(&auth.SIPGrant{Admin: true}) }

// Each method signs with the grant its server-sdk-go v2.19.0 counterpart uses, named beside it.

type signedRooms struct {
	c livekit.RoomService
	s signer
}

// ListRooms: roomclient.go, RoomList.
func (r signedRooms) ListRooms(ctx context.Context, req *livekit.ListRoomsRequest) (*livekit.ListRoomsResponse, error) {
	ctx, err := r.s.with(ctx, video(auth.VideoGrant{RoomList: true}))
	if err != nil {
		return nil, err
	}
	return r.c.ListRooms(ctx, req)
}

// ListParticipants: roomclient.go, RoomAdmin for the room.
func (r signedRooms) ListParticipants(ctx context.Context, req *livekit.ListParticipantsRequest) (*livekit.ListParticipantsResponse, error) {
	ctx, err := r.s.with(ctx, video(auth.VideoGrant{RoomAdmin: true, Room: req.GetRoom()}))
	if err != nil {
		return nil, err
	}
	return r.c.ListParticipants(ctx, req)
}

type signedDispatches struct {
	c livekit.AgentDispatchService
	s signer
}

// ListDispatch: agent_dispatch_client.go, RoomAdmin for the room.
func (d signedDispatches) ListDispatch(ctx context.Context, req *livekit.ListAgentDispatchRequest) (*livekit.ListAgentDispatchResponse, error) {
	ctx, err := d.s.with(ctx, video(auth.VideoGrant{RoomAdmin: true, Room: req.GetRoom()}))
	if err != nil {
		return nil, err
	}
	return d.c.ListDispatch(ctx, req)
}

type signedSIP struct {
	c livekit.SIP
	s signer
}

// The SIP lists: sipclient.go, SIP Admin.
func (p signedSIP) ListSIPInboundTrunk(ctx context.Context, req *livekit.ListSIPInboundTrunkRequest) (*livekit.ListSIPInboundTrunkResponse, error) {
	ctx, err := p.s.with(ctx, sipAdmin)
	if err != nil {
		return nil, err
	}
	return p.c.ListSIPInboundTrunk(ctx, req)
}

func (p signedSIP) ListSIPOutboundTrunk(ctx context.Context, req *livekit.ListSIPOutboundTrunkRequest) (*livekit.ListSIPOutboundTrunkResponse, error) {
	ctx, err := p.s.with(ctx, sipAdmin)
	if err != nil {
		return nil, err
	}
	return p.c.ListSIPOutboundTrunk(ctx, req)
}

func (p signedSIP) ListSIPDispatchRule(ctx context.Context, req *livekit.ListSIPDispatchRuleRequest) (*livekit.ListSIPDispatchRuleResponse, error) {
	ctx, err := p.s.with(ctx, sipAdmin)
	if err != nil {
		return nil, err
	}
	return p.c.ListSIPDispatchRule(ctx, req)
}

type signedNumbers struct {
	c livekit.PhoneNumberService
	s signer
}

// ListPhoneNumbers: phonenumberclient.go, SIP Admin.
func (n signedNumbers) ListPhoneNumbers(ctx context.Context, req *livekit.ListPhoneNumbersRequest) (*livekit.ListPhoneNumbersResponse, error) {
	ctx, err := n.s.with(ctx, sipAdmin)
	if err != nil {
		return nil, err
	}
	return n.c.ListPhoneNumbers(ctx, req)
}

type signedEgress struct {
	c livekit.Egress
	s signer
}

// ListEgress: egressclient.go, RoomRecord.
func (e signedEgress) ListEgress(ctx context.Context, req *livekit.ListEgressRequest) (*livekit.ListEgressResponse, error) {
	ctx, err := e.s.with(ctx, video(auth.VideoGrant{RoomRecord: true}))
	if err != nil {
		return nil, err
	}
	return e.c.ListEgress(ctx, req)
}

type signedIngress struct {
	c livekit.Ingress
	s signer
}

// ListIngress: ingressclient.go, IngressAdmin.
func (i signedIngress) ListIngress(ctx context.Context, req *livekit.ListIngressRequest) (*livekit.ListIngressResponse, error) {
	ctx, err := i.s.with(ctx, video(auth.VideoGrant{IngressAdmin: true}))
	if err != nil {
		return nil, err
	}
	return i.c.ListIngress(ctx, req)
}
