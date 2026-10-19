import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Plus } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { errorText } from "@/components/page-state"
import { createProject } from "@/lib/api"
import type { WorkspaceProject } from "@/lib/api-types"

// The new project's first page. A full page load, as the "No such project" link does, so the console
// reads the workspaces again and the new project is one it knows.
const openNewProject = (orgId: string, project: WorkspaceProject) => window.location.assign(`/org/${orgId}/project/${project.id}/overview`)

// Adds a LiveKit project to an organisation through createProject (phase 06f): its name, URL, API key
// and secret. web checks the fields as voicegateway project add does and seals the secret; the form
// holds the secret only until it is sent, and clears it after every submit. Admins only: callers show
// it to an Admin of the organisation.
export function AddProjectForm({ orgId, onCreated = (p) => openNewProject(orgId, p) }: { orgId: string; onCreated?: (project: WorkspaceProject) => void }) {
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const [key, setKey] = useState("")
  const [secret, setSecret] = useState("")
  const [problem, setProblem] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: (body: { name: string; livekitUrl: string; livekitApiKey: string; livekitApiSecret: string }) => createProject(orgId, body),
    onSuccess: (p) => onCreated(p),
    onError: (e: Error) => setProblem(errorText(e)),
  })
  const ready = name.trim() !== "" && url.trim() !== "" && key.trim() !== "" && secret.trim() !== "" && !create.isPending
  return (
    <form
      aria-label="Add project"
      className="w-full text-left"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ready) return
        setProblem(null)
        create.mutate({ name: name.trim(), livekitUrl: url.trim(), livekitApiKey: key.trim(), livekitApiSecret: secret })
        setSecret("")
      }}
    >
      <FieldGroup className="gap-4">
        <Field>
          <FieldLabel htmlFor="new-project-name">Name</FieldLabel>
          <Input id="new-project-name" placeholder="Production" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" maxLength={64} />
        </Field>
        <Field>
          <FieldLabel htmlFor="new-project-url">LiveKit URL</FieldLabel>
          <Input id="new-project-url" placeholder="wss://your-project.livekit.cloud" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="new-project-key">API key</FieldLabel>
            <Input id="new-project-key" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
          <Field>
            <FieldLabel htmlFor="new-project-secret">API secret</FieldLabel>
            <Input id="new-project-secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />
          </Field>
        </div>
        <FieldDescription>The secret is encrypted on the server and never shown again. Each project needs its own LiveKit API key.</FieldDescription>
        {problem && <FieldError>{problem}</FieldError>}
        <Button type="submit" disabled={!ready}>
          {create.isPending ? <Spinner /> : <Plus />} Add project
        </Button>
      </FieldGroup>
    </form>
  )
}
