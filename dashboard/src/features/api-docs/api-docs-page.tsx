import { ApiReferenceReact } from "@scalar/api-reference-react"
import "@scalar/api-reference-react/style.css"
import spec from "../../../../docs/api/openapi.yaml?raw"

// The API reference, rendered by Scalar from docs/api/openapi.yaml: the contract the console's types
// are generated from and web's responses are tested against. Requests sent from it go to this origin
// with the signed-in session, like the console's own. Nothing loads from Scalar's servers: no fonts,
// no telemetry, no request proxy, no AI chat or MCP offer.
export function ApiDocsPage() {
  return (
    <ApiReferenceReact
      configuration={{
        content: spec,
        withDefaultFonts: false,
        telemetry: false,
        showDeveloperTools: "never",
        hideClientButton: true,
        documentDownloadType: "yaml",
        agent: { disabled: true },
        mcp: { disabled: true },
      }}
    />
  )
}
