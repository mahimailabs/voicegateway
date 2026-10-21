// @vitest-environment jsdom
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { expect, test, vi } from "vitest"
import { DataTable, type Columns } from "../src/components/data-table"

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as MediaQueryList
})

type Row = { id: string; tag: string }
const columns: Columns<Row> = [
  { id: "name", header: "Name", cell: ({ row }) => row.original.id, meta: { expandedContent: (r: Row) => <div>{`tag ${r.tag}`}</div> } },
]

function Table() {
  const [rows, setRows] = useState<Row[]>([{ id: "slow", tag: "none" }])
  return (
    <>
      <button type="button" onClick={() => setRows([{ id: "slow", tag: "support" }])}>save tag</button>
      <DataTable data={rows} columns={columns} getRowId={(r) => r.id} canExpand={() => true} />
    </>
  )
}

test("an open row stays open when a save refreshes its data", async () => {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({ routeTree: root.addChildren([createRoute({ getParentRoute: () => root, path: "/", component: Table })]), history: createMemoryHistory({ initialEntries: ["/"] }) })
  render(<RouterProvider router={router} />)
  const user = userEvent.setup()
  await user.click(await screen.findByText("slow"))
  expect(await screen.findByText("tag none")).toBeTruthy()
  await user.click(screen.getByRole("button", { name: "save tag" }))
  expect(await screen.findByText("tag support")).toBeTruthy()
})
