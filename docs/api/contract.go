// Package api embeds the API contract, docs/api/openapi.yaml, in the binary, so the MCP server
// builds its tools from the same file the console's /docs page renders.
package api

import _ "embed"

// OpenAPI is docs/api/openapi.yaml.
//
//go:embed openapi.yaml
var OpenAPI []byte
