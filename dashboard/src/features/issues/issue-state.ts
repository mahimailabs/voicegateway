import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ApiError, saveIssueMetadata } from "@/lib/api"
import type { IssueMetadata, UpdateIssueMetadata } from "@/lib/api-types"
import { currentScope } from "@/lib/url-state"
import { roleIn, useSession } from "@/lib/session"

type Edit = {id: string; before: IssueMetadata; saved: IssueMetadata; decisionChanged: boolean}
type Change = {id: string; before: IssueMetadata; state?: IssueMetadata["state"]; tags?: string[]; archived?: boolean; undo?: Edit; scope?: ReturnType<typeof currentScope>}
export function useIssueMetadata() {
 const selected = {...currentScope()}
 const client = useQueryClient()
 const role = roleIn(selected.org,useSession())
 const canEdit = role === "Admin" || role === "Member"
 const mutation = useMutation({
  mutationFn: async (change: Change): Promise<Edit> => {
   const current = currentScope(); const expected = change.scope ?? selected
   if (current.org !== expected.org || current.project !== expected.project) throw new Error("Project changed. Reopen the issue before editing.")
   if (!canEdit) throw new Error("A Member or Admin can change issue metadata.")
   const before = change.undo?.saved ?? change.before
   const target = change.undo?.before
   const body: UpdateIssueMetadata = {tags:target?.tags ?? change.tags ?? before.tags,archived:target?.archived ?? change.archived ?? before.archived,revision:before.revision,evidenceRevision:before.evidenceRevision}
   if ((target && change.undo?.decisionChanged) || change.state !== undefined) body.state = target?.state ?? change.state
   const saved = await saveIssueMetadata(change.id, body, selected)
   await client.invalidateQueries({queryKey:["issues",selected]})
   return {id:change.id,before,saved,decisionChanged:body.state != null}
  },
  onError: async (error) => {
   if (!(error instanceof ApiError) || ![401,403].includes(error.status)) return
   const key = ["issues",selected]
   await client.cancelQueries({queryKey:key})
   for (const query of client.getQueryCache().findAll({queryKey:key})) query.setState({data:undefined,error,status:"error"})
  },
 })
 return {...mutation,canEdit,selected,mutateAsync:(change:Change)=>mutation.mutateAsync({...change,scope:selected})}
}
