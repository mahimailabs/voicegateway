import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ApiError, metadataQuery, saveCallMetadata } from "@/lib/api"
import type { CallMetadata } from "@/lib/api-types"
import { currentScope } from "@/lib/url-state"
import { roleIn, useSession } from "@/lib/session"

export type MetadataEdit = { id: string; before: CallMetadata; saved: CallMetadata }
type MetadataChange = {
  id: string
  tags?: string[]
  archived?: boolean
  expected?: CallMetadata
  undo?: MetadataEdit
  scope?: ReturnType<typeof currentScope>
}

export function useCallMetadata() {
  const selected = { ...currentScope() }
  const client = useQueryClient()
  const role = roleIn(selected.org, useSession())
  const canEdit = role === "Admin" || role === "Member"
  const assertScope = (scope = selected) => {
    const current = currentScope()
    if (scope.org !== current.org || scope.project !== current.project) {
      throw new Error("Project changed. Reopen the call before editing.")
    }
  }
  const refresh = async (id: string) => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["calls", selected] }),
      client.invalidateQueries({ queryKey: ["call", selected, id] }),
      client.invalidateQueries({ queryKey: ["call-tags", selected] }),
    ])
  }
  const save = async (id: string, before: CallMetadata, next: CallMetadata): Promise<MetadataEdit> => {
    assertScope()
    if (!canEdit) throw new Error("A Member or Admin can change call metadata.")
    const saved = await saveCallMetadata(id, next, selected)
    await client.cancelQueries({ queryKey: ["call-metadata", selected, id], exact: true })
    client.setQueryData<CallMetadata>(["call-metadata", selected, id], prior =>
      prior && prior.revision > saved.revision ? prior : saved,
    )
    await refresh(id)
    return { id, before, saved }
  }
  const mutation = useMutation({
    mutationFn: async (change: MetadataChange) => {
      assertScope(change.scope)
      if (change.undo) {
        return save(change.id, change.undo.saved, { ...change.undo.before, revision: change.undo.saved.revision })
      }
      const before = change.expected ?? await client.fetchQuery(metadataQuery(change.id, selected))
      if (!before) throw new Error("Call metadata could not be loaded.")
      return save(change.id, before, {
        ...before,
        ...(change.tags ? { tags: change.tags } : {}),
        ...(change.archived !== undefined ? { archived: change.archived } : {}),
      })
    },
    onError: async (error, change) => {
      if (!(error instanceof ApiError) || ![401, 403, 404].includes(error.status)) return
      const keys = [["call-metadata", selected, change.id], ["call", selected, change.id], ["call-tags", selected], ["calls", selected]]
      // Cancel reads first so an older response cannot restore denied or removed metadata.
      for (const key of keys) {
        await client.cancelQueries({ queryKey: key })
        for (const query of client.getQueryCache().findAll({ queryKey: key })) {
          query.setState({ data: undefined, error, status: "error" })
        }
      }
    },
  })
  return {
    ...mutation,
    mutateAsync: (change: MetadataChange) => mutation.mutateAsync({ ...change, scope: selected }),
    canEdit,
    selected,
  }
}
