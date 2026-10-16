# The voicegateway binary. Built from the repository root: docker build -f deploy/Dockerfile .
FROM golang:1.26.5-alpine@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS build
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
