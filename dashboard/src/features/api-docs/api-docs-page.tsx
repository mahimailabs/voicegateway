import type { CSSProperties } from "react"
import { Link } from "@tanstack/react-router"
import { ArrowLeft } from "@phosphor-icons/react"
import { ApiReferenceReact } from "@scalar/api-reference-react"
import "@scalar/api-reference-react/style.css"
import spec from "../../../../docs/api/openapi.yaml?raw"
import { Button } from "@/components/ui/button"

// The API reference, rendered by Scalar from docs/api/openapi.yaml: the contract the console's types
// are generated from and web's responses are tested against. Requests sent from it go to this origin
// with the signed-in session, like the console's own. Nothing loads from Scalar's servers: no fonts,
// no telemetry, no request proxy, no AI chat or MCP offer.
// Scalar keeps its sidebar and sticky headings below a header of this height.
const header = { "--scalar-custom-header-height": "3rem" } as CSSProperties

export function ApiDocsPage() {
  return (
    <div style={header}>
      <header className="sticky top-0 z-50 flex h-12 items-center gap-2 border-b bg-background px-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/">
            <ArrowLeft />
            Back to console
          </Link>
        </Button>
        <span className="text-sm text-muted-foreground">API reference</span>
      </header>
      <ApiReferenceReact
        configuration={{
          content: spec,
          withDefaultFonts: false,
          telemetry: false,
          // Keep Scalar secondary text readable in both themes.
          customCss: ".light-mode { --scalar-color-3: #6e6e73; } .dark-mode { --scalar-color-3: #a1a1a6; }",
          showDeveloperTools: "never",
          hideClientButton: true,
          documentDownloadType: "yaml",
          agent: { disabled: true },
          mcp: { disabled: true },
        }}
      />
    </div>
  )
}
