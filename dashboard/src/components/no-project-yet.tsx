import { TreeStructure } from "@phosphor-icons/react"
import { AddProjectForm } from "@/components/add-project-form"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import type { WorkspaceProject } from "@/lib/api-types"
import { roleIn, useSession } from "@/lib/session"

// An organisation with no project yet. An Admin adds one here (createProject, phase 06f); anyone else
// is told an admin adds projects. Onboarding never shows self-hosted commands or text, on any install:
// the server's own `voicegateway project add` is documented in the README.
export function NoProjectYet({ org, onCreated }: { org: { id: string; name: string } | null; onCreated?: (project: WorkspaceProject) => void }) {
  const session = useSession()
  const admin = org !== null && roleIn(org.id, session) === "Admin" ? org : null
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
              : "A project is one LiveKit project. You are not in an organisation with a project yet; ask an admin to invite you."}
        </EmptyDescription>
      </EmptyHeader>
      {admin && (
        <EmptyContent className="w-full max-w-xl">
          <AddProjectForm orgId={admin.id} onCreated={onCreated} />
        </EmptyContent>
      )}
    </Empty>
  )
}
