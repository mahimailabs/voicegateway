import type { Icon } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Spinner } from "@/components/ui/spinner"
import { ApiError } from "@/lib/api"

// A page, or a part of one, while its data loads, when it could not be read, or when it holds nothing
// yet. Every page that reads the API shows the same three.

export const errorText = (e: unknown) =>
  e instanceof ApiError && e.status === 0 ? "The VoiceGateway API did not answer. Check that web is running and reachable from this page." : e instanceof Error ? e.message : "Unknown error"

export function PageState({ icon: I, loading, title, detail, retry, className }: { icon: Icon; loading?: boolean; title: string; detail?: string; retry?: () => void; className?: string }) {
  return (
    <Empty className={className ?? "mx-auto max-w-[1200px]"}>
      <EmptyHeader>
        <EmptyMedia variant="icon">{loading ? <Spinner /> : <I />}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {detail && <EmptyDescription>{detail}</EmptyDescription>}
      </EmptyHeader>
      {retry && (
        <EmptyContent>
          <Button size="sm" variant="outline" onClick={retry}>
            Try again
          </Button>
        </EmptyContent>
      )}
    </Empty>
  )
}

// The usual pair: loading, then either the error with a retry, for a page whose read failed.
export function QueryState({ icon, what, query }: { icon: Icon; what: string; query: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown } }) {
  if (query.isPending) return <PageState icon={icon} loading title={`Loading ${what}`} />
  return <PageState icon={icon} title={`${what[0].toUpperCase()}${what.slice(1)} could not be loaded`} detail={errorText(query.error)} retry={() => void query.refetch()} />
}
