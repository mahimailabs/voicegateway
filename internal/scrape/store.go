package scrape

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/redis/go-redis/v9"

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

// Locker takes a lock for ttl if nobody holds it, so one worker reads a layer per interval, and
// swaps a value shared by the workers, returning the one before.
type Locker interface {
	Lock(ctx context.Context, key string, ttl time.Duration) (bool, error)
	Swap(ctx context.Context, key, value string, ttl time.Duration) (string, error)
}

// LiveKit is the part of a LiveKit server's API the media server read uses.
type LiveKit interface {
	ListRooms(ctx context.Context) ([]*livekit.Room, error)
	ListParticipants(ctx context.Context, room string) ([]*livekit.ParticipantInfo, error)
	ListDispatch(ctx context.Context, room string) ([]*livekit.AgentDispatch, error)
}

// Dial returns a LiveKit client for one project.
type Dial func(url, apiKey, apiSecret string) LiveKit

// DialSDK reads LiveKit through its Go server SDK's own clients.
func DialSDK(url, apiKey, apiSecret string) LiveKit {
	return sdkClient{
		rooms:      lksdk.NewRoomServiceClient(url, apiKey, apiSecret),
		dispatches: lksdk.NewAgentDispatchServiceClient(url, apiKey, apiSecret),
	}
}

type sdkClient struct {
	rooms      *lksdk.RoomServiceClient
	dispatches *lksdk.AgentDispatchClient
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
