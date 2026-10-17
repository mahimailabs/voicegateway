# The voicegateway binary. Built from the repository root: docker build -f deploy/Dockerfile .
FROM golang:1.26.8-alpine@sha256:8ac98ca534ac3f51e1f420a1dd2c15e74c75cfa0f23f3ad27eb5d7236c349a0c AS build
WORKDIR /src
COPY go.mod go.sum ./
COPY cmd ./cmd
COPY internal ./internal
COPY migrations ./migrations
# Cache mounts keep downloaded modules and build results between builds without baking them into a
# layer, so a source change rebuilds only what changed.
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/voicegateway ./cmd/voicegateway

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /out/voicegateway /usr/local/bin/voicegateway
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/voicegateway"]
