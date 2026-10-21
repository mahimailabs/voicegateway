// Package mcp serves VoiceGateway's MCP server at /mcp, so a coding agent can ask the questions the
// console answers (PRODUCT.md, MCP access). It holds no reads of its own: each tool is one
// operation of docs/api/openapi.yaml that a project API key may call, and calling it runs that
// operation through the /api/v1 router, behind the same role table.
//
// Rules: a caller is a project API key, identified by the same gate as /api/v1 (decision 0027); a
// tool reads the key's own project only, and a refusal or a missing value reaches the agent as the
// API answered it, never as an empty success.
package mcp
