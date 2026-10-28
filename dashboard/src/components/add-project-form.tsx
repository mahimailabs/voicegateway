import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Plus } from "@phosphor-icons/react"
import { humanId } from "human-id"
import { Button } from "@/components/ui/button"
import { Field, FieldContent, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { errorText } from "@/components/page-state"
import { LiveKitPaste } from "@/components/livekit-paste"
import { type LiveKitEnv, NOTHING_PASTED, filledText, namesLiveKit, pasteLiveKit } from "@/lib/livekit-env"
import { createProject } from "@/lib/api"
import type { ProjectCreate, WorkspaceProject } from "@/lib/api-types"

// The new project's first page. A full page load, as the "No such project" link does, so the console
// reads the workspaces again and the new project is one it knows.
const openNewProject = (orgId: string, project: WorkspaceProject) => window.location.assign(`/org/${orgId}/project/${project.id}/overview`)

// Adds a LiveKit project to an organisation through createProject (phase 06f): its name, URL, API key
// and secret. web checks the fields as voicegateway project add does, checks connectivity with one
// LiveKit read (a 422 when it does not verify or the URL gives no answer) and seals it; the form
// holds the secret only until it is sent, and clears it after every submit. Admins only: callers show
// it to an Admin of the organisation. Recordings go to the instance's bucket unless the admin brings
// their own S3 bucket here; web proves it with a write and a delete, and it can never be changed
// afterwards, so the form says so. Its secret is held and cleared like the LiveKit one.
// LiveKit Cloud's "Environment variables" block, pasted into the box at the top or into any of the
// three LiveKit inputs, fills all three; the name starts as a readable generated one (human-id), so
// a paste and Add project is enough.
export function AddProjectForm({ orgId, onCreated = (p) => openNewProject(orgId, p) }: { orgId: string; onCreated?: (project: WorkspaceProject) => void }) {
  const [name, setName] = useState(() => humanId({ separator: "-", capitalize: false }))
  const [url, setUrl] = useState("")
  const [key, setKey] = useState("")
  const [secret, setSecret] = useState("")
  const [ownBucket, setOwnBucket] = useState(false)
  const [endpoint, setEndpoint] = useState("")
  const [bucket, setBucket] = useState("")
  const [region, setRegion] = useState("")
  const [accessKey, setAccessKey] = useState("")
  const [bucketSecret, setBucketSecret] = useState("")
  const [problem, setProblem] = useState<string | null>(null)
  const [pasted, setPasted] = useState<string | null>(null)
  const fill = (env: LiveKitEnv) => {
    if (!namesLiveKit(env)) return setPasted(NOTHING_PASTED)
    if (env.url !== undefined) setUrl(env.url)
    if (env.apiKey !== undefined) setKey(env.apiKey)
    if (env.apiSecret !== undefined) setSecret(env.apiSecret)
    setPasted(filledText(env))
  }
  const onPaste = pasteLiveKit(fill)
  const create = useMutation({
    mutationFn: (body: ProjectCreate) => createProject(orgId, body),
    onSuccess: (p) => onCreated(p),
    onError: (e: Error) => setProblem(errorText(e)),
  })
  const bucketReady = !ownBucket || (endpoint.trim() !== "" && bucket.trim() !== "" && accessKey.trim() !== "" && bucketSecret.trim() !== "")
  const ready = name.trim() !== "" && url.trim() !== "" && key.trim() !== "" && secret.trim() !== "" && bucketReady && !create.isPending
  return (
    <form
      aria-label="Add project"
      className="w-full text-left"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ready) return
        setProblem(null)
        const body: ProjectCreate = { name: name.trim(), livekitUrl: url.trim(), livekitApiKey: key.trim(), livekitApiSecret: secret }
        if (ownBucket) {
          body.recordingBucket = { endpoint: endpoint.trim(), bucket: bucket.trim(), accessKeyId: accessKey.trim(), secretAccessKey: bucketSecret }
          if (region.trim() !== "") body.recordingBucket.region = region.trim()
        }
        create.mutate(body)
        setSecret("")
        setBucketSecret("")
      }}
    >
      <FieldGroup className="gap-4">
        <LiveKitPaste id="new-project-paste" onParsed={fill} note={pasted} />
        <Field>
          <FieldLabel htmlFor="new-project-name">Name</FieldLabel>
          <Input id="new-project-name" placeholder="Production" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" maxLength={64} />
        </Field>
        <Field>
          <FieldLabel htmlFor="new-project-url">LiveKit URL</FieldLabel>
          <Input id="new-project-url" placeholder="wss://your-project.livekit.cloud" value={url} onChange={(e) => setUrl(e.target.value)} onPaste={onPaste} autoComplete="off" spellCheck={false} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="new-project-key">API key</FieldLabel>
            <Input id="new-project-key" value={key} onChange={(e) => setKey(e.target.value)} onPaste={onPaste} autoComplete="off" spellCheck={false} />
          </Field>
          <Field>
            <FieldLabel htmlFor="new-project-secret">API secret</FieldLabel>
            <Input id="new-project-secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} onPaste={onPaste} autoComplete="new-password" />
          </Field>
        </div>
        <FieldDescription>VoiceGateway checks the key and secret with LiveKit, then encrypts the secret and never shows it again.</FieldDescription>
        <Field orientation="horizontal">
          <FieldContent>
            <FieldLabel htmlFor="new-project-own-bucket">Keep recordings in my own S3 bucket</FieldLabel>
            <FieldDescription>Off keeps them in VoiceGateway's bucket. Choose now: a project's bucket can't be changed later.</FieldDescription>
          </FieldContent>
          <Switch id="new-project-own-bucket" checked={ownBucket} onCheckedChange={setOwnBucket} />
        </Field>
        {ownBucket && (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="new-project-bucket-endpoint">S3 endpoint</FieldLabel>
                <Input id="new-project-bucket-endpoint" placeholder="https://s3.us-east-1.amazonaws.com" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} autoComplete="off" spellCheck={false} />
              </Field>
              <Field>
                <FieldLabel htmlFor="new-project-bucket-region">Region</FieldLabel>
                <Input id="new-project-bucket-region" placeholder="us-east-1" value={region} onChange={(e) => setRegion(e.target.value)} autoComplete="off" spellCheck={false} />
              </Field>
            </div>
            <Field>
              <FieldLabel htmlFor="new-project-bucket-name">Bucket</FieldLabel>
              <Input id="new-project-bucket-name" value={bucket} onChange={(e) => setBucket(e.target.value)} autoComplete="off" spellCheck={false} maxLength={63} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="new-project-bucket-key">Access key ID</FieldLabel>
                <Input id="new-project-bucket-key" value={accessKey} onChange={(e) => setAccessKey(e.target.value)} autoComplete="off" spellCheck={false} />
              </Field>
              <Field>
                <FieldLabel htmlFor="new-project-bucket-secret">Secret access key</FieldLabel>
                <Input id="new-project-bucket-secret" type="password" value={bucketSecret} onChange={(e) => setBucketSecret(e.target.value)} autoComplete="new-password" />
              </Field>
            </div>
            <FieldDescription>VoiceGateway writes and deletes one empty test object to check the key, then encrypts the secret. The key needs to write, read, list and delete objects under recordings/.</FieldDescription>
          </>
        )}
        {problem && <FieldError>{problem}</FieldError>}
        <Button type="submit" disabled={!ready}>
          {create.isPending ? (
            <>
              <Spinner /> Verifying with LiveKit
            </>
          ) : (
            <>
              <Plus /> Add project
            </>
          )}
        </Button>
      </FieldGroup>
    </form>
  )
}
