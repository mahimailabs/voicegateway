import { TreeStructure } from "@phosphor-icons/react"
import { AddProjectForm } from "@/components/add-project-form"
import { CopyCommand } from "@/components/copy-command"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Separator } from "@/components/ui/separator"
import type { WorkspaceProject } from "@/lib/api-types"
import { roleIn, useSession } from "@/lib/session"

// The command that adds a LiveKit project to a running install (README, make project-add), for the
// named organisation.
const projectAddCommand = (orgId: string) =>
  `docker compose exec -e LIVEKIT_API_SECRET web voicegateway project add${orgId === "default" ? "" : ` --org "${orgId}"`} --name "Production" --livekit-url "$LIVEKIT_URL" --livekit-api-key "$LIVEKIT_API_KEY"`

// An organisation with no project yet. An Admin adds one here (createProject, phase 06f), which a
// hosted admin with no shell needs; the server command stays below for a self-hosted install.
// Anyone else is told an admin adds projects. Without an organisation at all, only the command shows.
export function NoProjectYet({ org, onCreated }: { org: { id: string; name: string } | null; onCreated?: (project: WorkspaceProject) => void }) {
  const session = useSession()
  const admin = org !== null && roleIn(org.id, session) === "Admin" ? org : null
  const command = <CopyCommand text={projectAddCommand(org?.id ?? "default")} label="Project add command" />
  return (
    <Empty className="mx-auto max-w-[1200px]">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <TreeStructure />
        </EmptyMedia>
        <EmptyTitle>No project yet</EmptyTitle>
        <EmptyDescription>
          {admin
            ? `A project is one LiveKit project. Add one to ${admin.name} with its URL, API key and secret.`
            : org
              ? `A project is one LiveKit project. An admin of ${org.name} must add one.`
              : "A project is one LiveKit project. Add yours on the server, with its API secret in LIVEKIT_API_SECRET, then reload."}
        </EmptyDescription>
      </EmptyHeader>
      {admin ? (
        <EmptyContent className="w-full max-w-xl">
          <AddProjectForm orgId={admin.id} onCreated={onCreated} />
          <Separator />
          <p className="text-[13px] text-muted-foreground">Self-hosted? You can also add it on the server, with its API secret in LIVEKIT_API_SECRET:</p>
          {command}
        </EmptyContent>
      ) : (
        !org && <EmptyContent className="w-full max-w-xl">{command}</EmptyContent>
      )}
    </Empty>
  )
}
