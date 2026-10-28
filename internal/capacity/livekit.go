package capacity

import (
	"context"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/utils/xtwirp"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/livekit/server-sdk-go/v2/signalling"
	"github.com/twitchtv/twirp"
	"golang.org/x/sync/errgroup"
)

// testMetadata marks a test room, its dispatch and its caller, so an agent can tell a capacity
// test from a real call and skip side effects (decision 0022).
const testMetadata = `{"voicegateway":"capacity-test"}`

// emptyTimeout is how long LiveKit keeps a test room open once it is empty, in seconds, in case
// the runner dies before it deletes the room.
const emptyTimeout = 60

// NewDial returns how a run reaches a project's LiveKit API. Every Twirp call goes through client,
// which the worker builds with the same address guard as its other LiveKit reads
// (scrape.NewWorkerClient), and is signed with the one grant the server-sdk-go v2.19.0 method of
// the same name uses. server-sdk-go's room and dispatch clients take no http.Client, so the guarded
// client needs livekit/protocol's generated constructors, as in scrape.DialThrough.
func NewDial(client *http.Client) Dial {
	return func(url, apiKey, apiSecret string) LiveKit {
		base := signalling.ToHttpURL(url)
		opts := xtwirp.DefaultClientOptions()
		return &liveKit{
			rooms:      livekit.NewRoomServiceProtobufClient(base, client, opts...),
			dispatches: livekit.NewAgentDispatchServiceProtobufClient(base, client, opts...),
			client:     client, url: url, apiKey: apiKey, apiSecret: apiSecret,
		}
	}
}

type liveKit struct {
	rooms                  livekit.RoomService
	dispatches             livekit.AgentDispatchService
	client                 *http.Client
	url, apiKey, apiSecret string
}

func (l *liveKit) signed(ctx context.Context, g auth.VideoGrant) (context.Context, error) {
	token, err := auth.NewAccessToken(l.apiKey, l.apiSecret).SetVideoGrant(&g).ToJWT()
	if err != nil {
		return nil, fmt.Errorf("sign LiveKit request: %w", err)
	}
	return twirp.WithHTTPRequestHeaders(ctx, signalling.NewHTTPHeaderWithToken(token))
}

// CreateRoom: roomclient.go CreateRoom, RoomCreate.
func (l *liveKit) CreateRoom(ctx context.Context, name string) error {
	ctx, err := l.signed(ctx, auth.VideoGrant{RoomCreate: true})
	if err != nil {
		return err
	}
	_, err = l.rooms.CreateRoom(ctx, &livekit.CreateRoomRequest{Name: name, EmptyTimeout: emptyTimeout, DepartureTimeout: 1, Metadata: testMetadata})
	return err
}

// DeleteRoom: roomclient.go DeleteRoom, RoomCreate.
func (l *liveKit) DeleteRoom(ctx context.Context, name string) error {
	ctx, err := l.signed(ctx, auth.VideoGrant{RoomCreate: true})
	if err != nil {
		return err
	}
	_, err = l.rooms.DeleteRoom(ctx, &livekit.DeleteRoomRequest{Room: name})
	return err
}

// Dispatch: agent_dispatch_client.go CreateDispatch, RoomAdmin for the room.
func (l *liveKit) Dispatch(ctx context.Context, room, agent string) error {
	ctx, err := l.signed(ctx, auth.VideoGrant{RoomAdmin: true, Room: room})
	if err != nil {
		return err
	}
	_, err = l.dispatches.CreateDispatch(ctx, &livekit.CreateAgentDispatchRequest{Room: room, AgentName: agent, Metadata: testMetadata})
	return err
}

// listAtOnce bounds how many rooms' dispatches AgentRooms reads at the same time.
const listAtOnce = 8

// AgentRooms lists the project's rooms and reads each non-test room's dispatches: a room whose
// dispatch for the agent has a pending or running job has the agent (roomclient.go ListRooms,
// RoomList; agent_dispatch_client.go ListDispatch, RoomAdmin for the room).
func (l *liveKit) AgentRooms(ctx context.Context, agent string) ([]string, error) {
	lctx, err := l.signed(ctx, auth.VideoGrant{RoomList: true})
	if err != nil {
		return nil, err
	}
	res, err := l.rooms.ListRooms(lctx, &livekit.ListRoomsRequest{})
	if err != nil {
		return nil, fmt.Errorf("list rooms: %w", err)
	}
	var (
		mu   sync.Mutex
		busy []string
	)
	g, gctx := errgroup.WithContext(ctx)
	g.SetLimit(listAtOnce)
	for _, room := range res.GetRooms() {
		if IsTestRoom(room.GetName()) {
			continue
		}
		g.Go(func() error {
			dctx, err := l.signed(gctx, auth.VideoGrant{RoomAdmin: true, Room: room.GetName()})
			if err != nil {
				return err
			}
			got, err := l.dispatches.ListDispatch(dctx, &livekit.ListAgentDispatchRequest{Room: room.GetName()})
			if err != nil {
				return fmt.Errorf("list dispatch: %w", err)
			}
			if hasJob(got.GetAgentDispatches(), agent) {
				mu.Lock()
				busy = append(busy, room.GetName())
				mu.Unlock()
			}
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return nil, err
	}
	return busy, nil
}

func hasJob(ds []*livekit.AgentDispatch, agent string) bool {
	for _, d := range ds {
		if d.GetAgentName() != agent {
			continue
		}
		for _, j := range d.GetState().GetJobs() {
			switch j.GetState().GetStatus() {
			case livekit.JobStatus_JS_PENDING, livekit.JobStatus_JS_RUNNING:
				return true
			}
		}
	}
	return false
}

// CloudAgent asks LiveKit Cloud's agent API whether the agent is deployed there (agent_client.go
// ListAgents). A server without that API, or any error, leaves it unknown.
func (l *liveKit) CloudAgent(ctx context.Context, agent string) *bool {
	c, err := lksdk.NewAgentClient(l.url, l.apiKey, l.apiSecret, lksdk.WithHTTPClient(l.client))
	if err != nil {
		return nil
	}
	res, err := c.ListAgents(ctx, &livekit.ListAgentsRequest{})
	if err != nil {
		return nil
	}
	found := false
	for _, a := range res.GetAgents() {
		found = found || a.GetAgentName() == agent
	}
	return &found
}

// Call joins a synthetic caller to the room (caller.go).
func (l *liveKit) Call(ctx context.Context, room string, events chan<- Event) (func(), error) {
	return dialCaller(ctx, l.url, l.apiKey, l.apiSecret, room, events, time.Now)
}
