-- name: CreateProject :one
INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING organisation_id, id, name, livekit_url, livekit_api_key, created_at;

-- name: ProjectByLiveKitKey :one
SELECT organisation_id, id, name, livekit_url, livekit_api_key, created_at
FROM projects
WHERE livekit_api_key = $1;

-- name: SealedLiveKitSecret :one
SELECT livekit_api_secret_sealed
FROM projects
WHERE organisation_id = $1 AND id = $2;

-- name: OrganisationExists :one
SELECT EXISTS (SELECT 1 FROM organisations WHERE id = $1);
