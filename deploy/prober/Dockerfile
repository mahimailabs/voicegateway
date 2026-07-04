# Distributed SFU prober image.
#
# A thin image that runs `voicegw livekit sfu --report-to ...` against a
# coordinator. Deploy N of these across regions (see deploy/prober/fly.toml and
# the distributed-SFU docs) to load one SFU room concurrently from many
# vantages. The prober needs only the base install (httpx); the coordinator is
# the one that needs the [server] extra.
FROM python:3.12-slim

# Non-root: the prober only makes outbound connections.
RUN useradd --create-home --uid 10001 prober
WORKDIR /home/prober

# Pin the same engine version you run the coordinator with so the wire contract
# (register / job / report) matches. Override at build time: --build-arg VERSION=...
ARG VERSION=""
RUN pip install --no-cache-dir "voicegateway${VERSION:+==$VERSION}"

USER prober

# The coordinator URL and vantage label are per-region; set them at deploy time.
#   COORDINATOR_URL  e.g. http://coordinator.internal:8787
#   VOICEGW_REGION   the vantage label (Fly injects FLY_REGION; map it in fly.toml)
# LiveKit creds come from the standard env vars, same as every other command:
#   LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
ENV COORDINATOR_URL="" \
    VOICEGW_REGION=""

# sh -c so ${...} expands at runtime from the environment.
ENTRYPOINT ["sh", "-c", "voicegw livekit sfu --report-to \"$COORDINATOR_URL\" --vantage \"$VOICEGW_REGION\" --ramp \"${RAMP:-10,25,50}\" --duration \"${DURATION:-20}\""]
