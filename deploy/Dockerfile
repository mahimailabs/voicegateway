# The voicegateway binary and the console it serves (decision 0011). Built from the repository root:
# docker build -f deploy/Dockerfile .

# The console: Vite builds dashboard/ into static files. The API reference page imports
# docs/api/openapi.yaml, so that comes along.
FROM node:24.22.0-alpine3.24@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS console
WORKDIR /src/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY dashboard ./
COPY docs/api /src/docs/api
RUN npm run build

FROM golang:1.26.8-alpine@sha256:8ac98ca534ac3f51e1f420a1dd2c15e74c75cfa0f23f3ad27eb5d7236c349a0c AS build
WORKDIR /src
COPY go.mod go.sum ./
COPY cmd ./cmd
COPY internal ./internal
COPY ee ./ee
COPY migrations ./migrations
# The MCP server builds its tools from the API contract, embedded from docs/api.
COPY docs/api ./docs/api
# Cache mounts keep downloaded modules and build results between builds without baking them into a
# layer, so a source change rebuilds only what changed.
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/voicegateway ./cmd/voicegateway

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /out/voicegateway /usr/local/bin/voicegateway
COPY --from=console /src/dashboard/dist /usr/share/voicegateway/console
ENV VOICEGATEWAY_CONSOLE_DIR=/usr/share/voicegateway/console
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/voicegateway"]
