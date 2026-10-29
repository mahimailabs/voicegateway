# Distributed SFU prober image.
#
# A thin image that runs `voicegw livekit sfu --report-to ...` against a
# coordinator. Deploy one per AWS region (deploy/prober/cloudformation.yaml,
# decision 0037) to load one SFU room concurrently from many vantages. The
# prober needs only the base install (httpx); the coordinator is the one that
# needs the [server] extra.
FROM python:3.12-slim

# Non-root: the prober only makes outbound connections.
RUN useradd --create-home --uid 10001 prober
WORKDIR /home/prober

# Pin the same engine version you run the coordinator with so the wire contract
# (register / job / report) matches. Override at build time: --build-arg VERSION=...
# 0.27.0 is the last engine release with `voicegw livekit sfu`: from 1.0 the
# voicegateway package on PyPI is the agent SDK, which has no prober.
ARG VERSION=0.27.0
RUN pip install --no-cache-dir "voicegateway==${VERSION}"

USER prober

# The coordinator URL and vantage label are per-region; set them at deploy time.
#   COORDINATOR_URL  e.g. http://coordinator.internal:8787
#   VOICEGW_REGION   the vantage label (the stack sets it to its AWS region)
# LiveKit creds come from the standard env vars, same as every other command:
#   LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
# The ramp and duration are dictated by the coordinator's job (every vantage runs
# the same ones), so the prober takes no --ramp/--duration: set them once on the
# coordinator, not here.
ENV COORDINATOR_URL="" \
    VOICEGW_REGION=""

# sh -c so ${...} expands at runtime from the environment.
ENTRYPOINT ["sh", "-c", "voicegw livekit sfu --report-to \"$COORDINATOR_URL\" --vantage \"$VOICEGW_REGION\""]
