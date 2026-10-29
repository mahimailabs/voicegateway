# Distributed SFU prober image.
#
# A thin image that runs `voicegw livekit sfu --report-to ...` against a
# coordinator. Deploy one per AWS region (deploy/prober/cloudformation.yaml,
# decision 0037) to load one SFU room concurrently from many vantages. The
# prober needs the [livekit] extra (livekit.api and livekit.rtc); the
# coordinator is the one that also needs the [server] extra.
FROM python:3.12.16-slim@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07

# Non-root: the prober only makes outbound connections.
RUN useradd --create-home --uid 10001 prober
WORKDIR /home/prober

# requirements.txt locks voicegateway[livekit]==0.27.0 and every dependency
# with hashes, so two builds install the same packages. Run the coordinator on
# the same engine version so the wire contract (register / job / report)
# matches. 0.27.0 is the last engine release with `voicegw livekit sfu`: from
# 1.0 the voicegateway package on PyPI is the agent SDK, which has no prober.
# To change it, edit requirements.in and regenerate from deploy/prober/:
#   uv pip compile requirements.in --python-version 3.12 \
#       --python-platform x86_64-manylinux_2_28 --generate-hashes -o requirements.txt
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

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
