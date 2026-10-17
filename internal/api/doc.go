// Package api serves the console's reads under /api/v1 (docs/api/openapi.yaml). It reads only what
// the worker stored and never calls LiveKit on a request (ARCHITECTURE.md Boundaries).
//
// Rules: every route is scoped to the organisation and project in its path, and an unknown one is
// not_found; a value storage does not hold is null, never zero; one shared gate runs before every
// route.
package api
