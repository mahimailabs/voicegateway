# Releasing VoiceGateway

This document is for maintainers. Users should read [README.md](README.md).

## Prerequisites (one-time setup)

1. **Create Docker Hub repos:**
   - [mahimairaja/voicegateway](https://hub.docker.com/r/mahimairaja/voicegateway) (Public)
   - [mahimairaja/voicegateway-dashboard](https://hub.docker.com/r/mahimairaja/voicegateway-dashboard) (Public)

2. **Create a Docker Hub access token:**
   - Docker Hub → Account Settings → Security → New Access Token
   - Name: `github-actions-voicegateway`
   - Permissions: Read, Write, Delete

3. **Add GitHub secrets** to `mahimailabs/voicegateway`:
   - `DOCKERHUB_USERNAME` = `mahimairaja`
   - `DOCKERHUB_TOKEN` = the access token from step 2

## Cutting a release

1. Ensure `main` is green (all CI checks passing).

2. Update `docs/reference/changelog.md` with the new version's changes.

3. Commit the changelog:
   ```bash
   git add docs/reference/changelog.md
   git commit -m "chore: update changelog for v0.1.0"
   git push origin main
   ```

4. Create and push the tag:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

5. **GitHub Actions takes over:**
   - Runs the full test suite
   - Builds multi-arch Docker images (amd64 + arm64)
   - Pushes to Docker Hub as `:0.1.0`, `:0.1`, and `:latest`
   - Updates Docker Hub READMEs from `docker/README.*.md`
   - Creates a GitHub Release with auto-generated notes

   Monitor at: https://github.com/mahimailabs/voicegateway/actions

6. After the release publishes, verify:
   ```bash
   docker pull mahimairaja/voicegateway:0.1.0
   docker run --rm mahimairaja/voicegateway:0.1.0 voicegw --version
   ```

## Rolling back a release

If a release has a critical bug:

1. Delete the tag:
   ```bash
   git tag -d v0.1.0
   git push origin :refs/tags/v0.1.0
   ```

2. Delete the GitHub Release (via UI).

3. Overwrite the Docker Hub latest tag:
   ```bash
   docker pull mahimairaja/voicegateway:0.0.9  # previous good version
   docker tag mahimairaja/voicegateway:0.0.9 mahimairaja/voicegateway:latest
   docker push mahimairaja/voicegateway:latest
   ```

4. Cut a patch release with the fix.

## Testing locally before tagging

```bash
# Build just amd64 (fast)
docker build -t voicegateway:test -f Dockerfile .
docker run -p 8080:8080 voicegateway:test

# Dashboard
docker build -t dashboard:test -f dashboard/Dockerfile .
docker run -p 9090:9090 dashboard:test
```

## Manual republish (emergency)

If CI is broken:

```bash
docker login -u mahimairaja
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t mahimairaja/voicegateway:0.1.0 \
  -t mahimairaja/voicegateway:latest \
  -f Dockerfile \
  --push .
```
