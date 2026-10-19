import { useNavigate } from "@tanstack/react-router"
import { AddProjectForm } from "@/components/add-project-form"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { currentScope } from "@/lib/url-state"
import { roleIn, useSession } from "@/lib/session"

// A project is one LiveKit project, added with its URL, API key and secret (decision 0016) through
// createProject (phase 06f). The secret goes to VoiceGateway's server, which seals it, and is never
// kept in the browser; the form holds it only until it is sent. Adding is an Admin's; anyone else is
// told so.

export function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate()
  const session = useSession()
  const orgId = currentScope().org
  const admin = roleIn(orgId, session) === "Admin"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a LiveKit project</DialogTitle>
          <DialogDescription>{admin ? "Paste the URL and keys of a LiveKit Cloud project or your own server." : "An admin of this organisation adds projects."}</DialogDescription>
        </DialogHeader>
        {/* Closing the dialog unmounts the form, so nothing typed outlives it. */}
        {open && admin && <AddProjectForm orgId={orgId} />}
        <DialogFooter className="flex-col gap-2 sm:flex-col sm:items-stretch">
          <p className="text-center text-xs text-muted-foreground">
            <button
              type="button"
              className="text-primary hover:underline"
              onClick={() => {
                onOpenChange(false)
                navigate({ to: "/project" })
              }}
            >
              See an example project
            </button>
          </p>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
