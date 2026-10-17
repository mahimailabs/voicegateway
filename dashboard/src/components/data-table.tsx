import { useMemo, useState } from "react"
import type { ColumnDef, PaginationState, RowData, SortingState } from "@tanstack/react-table"
import { useTable } from "@tanstack/react-table"
import { useIsPhone } from "@/hooks/use-mobile"
import { DataGrid, DataGridContainer, dataGridFeatures, type DataGridFeatures } from "@/components/reui/data-grid/data-grid"
import { DataGridPagination } from "@/components/reui/data-grid/data-grid-pagination"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { DataGridScrollArea } from "@/components/reui/data-grid/data-grid-scroll-area"
import { DataGridTable } from "@/components/reui/data-grid/data-grid-table"
import { Checkbox } from "@/components/ui/checkbox"
import { Filters } from "@/components/reui/filters/filters"
import { createFilterQuery, flattenFilterConditions, type FilterCondition } from "@/components/reui/filters/filters-query"
import type { FilterField, FilterQuery } from "@/components/reui/filters/filters-types"
import { useUrlState } from "@/lib/url-state"

const emptyQuery = createFilterQuery([])

// Every list in the console is this one table: TanStack Table through ReUI's data grid (sorting,
// paging, expandable rows) under ReUI's filter chips. Pages describe columns and filter fields; the
// filtering, sorting and paging are the same everywhere.

export type Columns<T extends RowData> = ColumnDef<DataGridFeatures, T>[]
// A filter field plus how to read its value from a row. A null value is unknown: it matches only
// "is empty", never a comparison, so a filter never treats a missing number as zero.
export type Field<T> = FilterField & { get: (row: T) => unknown }

function test(v: unknown, c: FilterCondition): boolean {
  const known = v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0) && v !== ""
  if (c.operator === "empty") return !known
  if (c.operator === "not_empty") return known
  if (!known) return false
  const vs = c.values
  const list = Array.isArray(v) ? v.map(String) : [String(v)]
  const num = typeof v === "number" ? v : Number.NaN
  const text = list.join(" ").toLowerCase()
  const n = (i: number) => Number(vs[i])
  switch (c.operator) {
    case "is":
    case "eq":
      return list.includes(String(vs[0]))
    case "is_not":
    case "neq":
      return !list.includes(String(vs[0]))
    case "is_any_of":
    case "has_any_of":
      return vs.some((x) => list.includes(String(x)))
    case "is_none_of":
    case "has_none_of":
      return !vs.some((x) => list.includes(String(x)))
    case "has_all_of":
      return vs.every((x) => list.includes(String(x)))
    case "contains":
      return text.includes(String(vs[0] ?? "").toLowerCase())
    case "not_contains":
      return !text.includes(String(vs[0] ?? "").toLowerCase())
    case "starts_with":
      return text.startsWith(String(vs[0] ?? "").toLowerCase())
    case "ends_with":
      return text.endsWith(String(vs[0] ?? "").toLowerCase())
    case "gt":
      return num > n(0)
    case "gte":
      return num >= n(0)
    case "lt":
      return num < n(0)
    case "lte":
      return num <= n(0)
    case "between":
      return num >= n(0) && num <= n(1)
    case "not_between":
      return num < n(0) || num > n(1)
    default:
      return true
  }
}

export function matches<T>(row: T, query: FilterQuery, fields: Field<T>[]): boolean {
  const conditions = flattenFilterConditions(query)
  if (!conditions.length) return true
  const results = conditions.map((c) => {
    const f = fields.find((x) => x.id === c.field)
    if (!f) return true
    const r = test(f.get(row), c)
    return c.negated ? !r : r
  })
  return query.combinator === "or" ? results.some(Boolean) : results.every(Boolean)
}

export function DataTable<T extends RowData & object>({
  data,
  columns,
  fields = [],
  getRowId,
  onRowClick,
  canExpand,
  sort = [],
  pageSize = 25,
  empty = "Nothing matches these filters.",
  toolbar,
  query: controlled,
  onQueryChange,
  bulk,
  filtered = false,
  server,
  phoneColumns,
}: {
  data: T[]
  columns: Columns<T>
  fields?: Field<T>[]
  getRowId: (row: T) => string
  onRowClick?: (row: T) => void
  canExpand?: (row: T) => boolean
  sort?: SortingState
  pageSize?: number
  empty?: React.ReactNode
  toolbar?: React.ReactNode
  query?: FilterQuery
  onQueryChange?: (q: FilterQuery) => void
  // Row selection: a checkbox column, and these actions on a bar while any row is selected.
  bulk?: (selected: T[], clear: () => void) => React.ReactNode
  // The data already answers the query (the server filtered it); the chips only edit it.
  filtered?: boolean
  // The server sorts and pages: the table shows the rows read so far, sorts nothing itself and offers
  // the next page. Sort columns map to the server's sort keys.
  server?: { total: number | null; hasMore: boolean; loading: boolean; loadMore: () => void }
  // At phone width only these columns show (plus row selection), so a row stacks what matters in its
  // first cell instead of scrolling sideways.
  phoneColumns?: string[]
}) {
  const phone = useIsPhone()
  // Filters and sort live in the URL (?q=, ?sort=), so a shared link opens the same rows. Each page has
  // one table, so the keys never collide.
  const [own, setOwn] = useUrlState<FilterQuery>("q", emptyQuery)
  const query = controlled ?? own
  const setQuery = onQueryChange ?? setOwn
  const [sorting, setSortingUrl] = useUrlState<SortingState>("sort", sort)
  const setSorting = (u: SortingState | ((old: SortingState) => SortingState)) => setSortingUrl(typeof u === "function" ? u(sorting) : u)
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize })
  const rows = useMemo(() => (filtered ? data : data.filter((r) => matches(r, query, fields))), [data, query, fields, filtered])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  // Selection only counts rows still in view (not filtered out, archived or resolved).
  const selected = rows.filter((r) => picked.has(getRowId(r)))
  const clear = () => setPicked(new Set())
  const allOn = rows.length > 0 && selected.length === rows.length
  const cols = useMemo<Columns<T>>(
    () =>
      bulk
        ? [
            {
              id: "select",
              size: 36,
              enableSorting: false,
              header: () => <Checkbox aria-label="Select all" checked={allOn ? true : selected.length ? "indeterminate" : false} onCheckedChange={(v) => setPicked(v ? new Set(rows.map(getRowId)) : new Set())} />,
              cell: ({ row }) => {
                const id = getRowId(row.original)
                return (
                  <span onClick={(e) => e.stopPropagation()} className="flex">
                    <Checkbox
                      aria-label="Select row"
                      checked={picked.has(id)}
                      onCheckedChange={(v) =>
                        setPicked((p) => {
                          const n = new Set(p)
                          if (v) n.add(id)
                          else n.delete(id)
                          return n
                        })
                      }
                    />
                  </span>
                )
              },
            },
            ...columns,
          ]
        : columns,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bulk, columns, picked, rows, allOn],
  )

  const table = useTable({
    features: dataGridFeatures,
    columns: cols,
    data: rows,
    getRowId,
    getRowCanExpand: canExpand ? (row) => canExpand(row.original) : undefined,
    // Server mode shows every row read so far on one page; "Show more" reads the next.
    state: {
      sorting,
      pagination: server ? { pageIndex: 0, pageSize: Math.max(rows.length, 1) } : pagination,
      columnVisibility: phone && phoneColumns ? Object.fromEntries(cols.map((c) => [c.id ?? "", c.id === "select" || phoneColumns.includes(c.id ?? "")])) : {},
    },
    manualSorting: Boolean(server),
    // The server always sorts by something, so a column cannot be un-sorted.
    enableSortingRemoval: !server,
    onSortingChange: setSorting,
    onPaginationChange: (u) => setPagination(u),
  })

  return (
    <div className="space-y-3">
      {bulk && selected.length > 0 && (
        <div className="sticky top-14 z-20 flex flex-wrap items-center gap-2 rounded-xl border bg-background/95 px-3 py-2 shadow-sm backdrop-blur animate-in fade-in slide-in-from-top-1">
          <span className="text-[13px] font-medium tnum">{`${selected.length} selected`}</span>
          <span className="mx-1 h-4 w-px bg-border" />
          {bulk(selected, clear)}
          <button type="button" onClick={clear} className="ml-auto text-[12px] text-muted-foreground hover:text-foreground">
            Clear
          </button>
        </div>
      )}
      {(fields.length > 0 || toolbar) && (
        <div className="flex flex-wrap items-start gap-3">
          {fields.length > 0 && (
            <div className="min-w-0 flex-1">
              <Filters
                fields={fields}
                query={query}
                onQueryChange={(q) => {
                  setQuery(q)
                  setPagination((p) => ({ ...p, pageIndex: 0 }))
                }}
                showClear
              />
            </div>
          )}
          {toolbar}
        </div>
      )}
      <DataGrid
        table={table}
        recordCount={rows.length}
        onRowClick={
          onRowClick
            ? (r) => {
                // The grid keeps keyboard focus on click; a drawer opened over it would hide a focused element.
                ;(document.activeElement as HTMLElement | null)?.blur()
                onRowClick(r)
              }
            : canExpand
              ? (r) => table.getRow(getRowId(r)).toggleExpanded()
              : undefined
        }
        emptyMessage={empty}
        tableLayout={{ headerBackground: false, rowBorder: true, width: "auto" }}
      >
        <div className="w-full space-y-2.5">
          <DataGridContainer className="rounded-xl">
            <DataGridScrollArea>
              <DataGridTable />
            </DataGridScrollArea>
          </DataGridContainer>
          {!server && rows.length > pagination.pageSize && <DataGridPagination />}
          {server && (server.hasMore || server.total !== null) && (
            <div className="flex items-center justify-between gap-3 text-[12px] text-muted-foreground">
              <span className="tnum">{server.total === null ? `${rows.length} shown` : `${rows.length} of ${server.total}`}</span>
              {server.hasMore && (
                <Button size="sm" variant="outline" disabled={server.loading} onClick={server.loadMore}>
                  {server.loading ? <Spinner /> : null} Show more
                </Button>
              )}
            </div>
          )}
        </div>
      </DataGrid>
    </div>
  )
}
