import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { execFileSync } from "node:child_process"
import { fileURLToPath } from "node:url"
import { Ajv2020 } from "ajv/dist/2020.js"
import { describe, expect, test } from "vitest"
import { parse, stringify } from "yaml"
import { checkCoverage, checkSpec } from "../scripts/check-sources.ts"

// The spec is plain YAML data; tests mutate it freely.
type Doc = Record<string, any>
const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, "../..")
const contract = path.join(root, "docs/api/openapi.yaml")
const load = () => parse(fs.readFileSync(contract, "utf8")) as Doc
const json = (file: string) => JSON.parse(fs.readFileSync(path.join(root, file), "utf8"))

// Runs the pinned Redocly CLI with redocly.yaml on a changed copy of the contract.
function redocly(spec: Doc) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vg-contract-"))
  const file = path.join(dir, "openapi.yaml")
  fs.writeFileSync(file, stringify(spec))
  const bin = path.join(root, "dashboard/node_modules/.bin/redocly")
  try {
    execFileSync(bin, ["lint", file, "--config", path.join(root, "redocly.yaml"), "--format", "stylish"], { encoding: "utf8", stdio: "pipe", env: { ...process.env, REDOCLY_TELEMETRY: "off" } })
    return ""
  } catch (error) {
    const out = error as { stdout?: string; stderr?: string }
    return `${out.stdout ?? ""}${out.stderr ?? ""}`
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

test("source checker accepts the contract and checks nested properties", () => {
  const result = checkSpec(load())
  expect(result.errors).toEqual([])
  expect(result.properties > 0).toBeTruthy()
})

test("source checker rejects missing attribution inside nested arrays", () => {
  const spec = load()
  const schema = spec.components.schemas
  delete schema.Workers.properties.workers.items.properties.samples.items.properties.load["x-source"]
  expect(checkSpec(spec).errors.join("\n")).toMatch(/samples.*load.*missing structured x-source/)
})

test("source checker rejects invented evidence and unresolved references", () => {
  const spec = load()
  spec.components.schemas.Call.properties.room["x-source"].pointer = "/no-such-captured-field"
  spec.components.schemas.Call.properties.turns.items.$ref = "#/components/schemas/Invented"
  const errors = checkSpec(spec).errors.join("\n")
  expect(errors).toMatch(/source JSON pointer does not resolve/)
  expect(errors).toMatch(/unresolved schema reference/)
})

test("source checker rejects zero defaults, non-nullable metrics and missing units", () => {
  const spec = load()
  const metric = spec.components.schemas.Turn.properties.ttsMs
  metric.type = "number"
  metric.default = 0
  delete metric["x-unit"]
  const errors = checkSpec(spec).errors.join("\n")
  expect(errors).toMatch(/measurement must allow null/)
  expect(errors).toMatch(/cannot default to zero/)
  expect(errors).toMatch(/lacks a unit/)
})

test("source checker rejects a public read", () => {
  const spec = load()
  delete (Object.values(spec.paths)[0] as Doc).get["x-access"]
  expect(checkSpec(spec).errors.join("\n")).toMatch(/mandatory authorisation/)
})

test("read inventory covers actual sources, routes and widget ids", () => {
  expect(checkCoverage(load()).errors).toEqual([])
})

test("read inventory rejects an omitted reader and a removed endpoint", () => {
  const spec = load()
  const inventory = json("docs/api/readers.json")
  const omitted = inventory.pop()
  const sources = checkCoverage(spec, root, inventory).errors.join("\n")
  expect(sources.includes(omitted.file)).toBeTruthy()
  delete spec.paths["/api/v1/org/{orgId}/project/{projectId}/calls"]
  expect(checkCoverage(spec).errors.join("\n")).toMatch(/unknown operation listCalls/)
})

test("route coverage rejects new or stale page and widget entries", () => {
  const surfaces = json("docs/api/surfaces.json")
  delete surfaces.routes["/calls"]
  surfaces.routes["/retired-page"] = ["listCalls"]
  surfaces.widgets.push("unimplemented-widget")
  const errors = checkCoverage(load(), root, undefined, surfaces).errors.join("\n")
  expect(errors).toMatch(/calls: missing page-to-endpoint/)
  expect(errors).toMatch(/retired-page: stale route/)
  expect(errors).toMatch(/widget inventory drift/)
})

test("source checker rejects directories and contradictory captured discriminators", () => {
  const spec = load()
  spec.components.schemas.Call.properties.id["x-source"].file = "docs/api"
  spec.components.schemas.TranscriptEvent.properties.type.const = "invented_event"
  const errors = checkSpec(spec).errors.join("\n")
  expect(errors).toMatch(/source evidence does not exist: docs\/api/)
  expect(errors).toMatch(/constant contradicts its captured evidence/)
})

test("source checker rejects a project endpoint with an organisation-only access rule", () => {
  const spec = load()
  const access = spec.paths["/api/v1/org/{orgId}/project/{projectId}/calls"].get["x-access"]
  access.scope = "organisation"
  delete access.projectIsolation
  access.minimumRole = "unrestricted"
  const errors = checkSpec(spec).errors.join("\n")
  expect(errors).toMatch(/scope does not match/)
  expect(errors).toMatch(/project isolation is mandatory/)
  expect(errors).toMatch(/supported organisation role/)
})

test("relative positions require clock bindings while durations remain intervals", () => {
  const spec = load()
  delete spec.components.schemas.OverlapEvent.properties.detectedAt["x-clock"]
  spec.components.schemas.Span.required = spec.components.schemas.Span.required.filter((key: string) => key !== "clockId")
  const errors = checkSpec(spec).errors.join("\n")
  expect(errors).toMatch(/detectedAt.*resolvable clock reference/)
  expect(errors).toMatch(/startMs.*clock reference must be required/)
  expect(spec.components.schemas.OverlapEvent.properties.detectionDelay["x-unit"]).toBe("seconds")
})

test("clock schema preserves unknown anchors and rejects fabricated alignment", () => {
  const validate = new Ajv2020({ strict: false, validateFormats: false }).compile(load().components.schemas.Clock)
  const unknown = { id: "events", origin: "first_event", originAt: null, offsetToCallStartSeconds: null, alignment: "unknown" }
  expect(validate(unknown)).toBe(true)
  expect(validate({ ...unknown, offsetToCallStartSeconds: 0 })).toBe(false)
  expect(validate({ ...unknown, alignment: "observed" })).toBe(false)
  expect(validate({ ...unknown, alignment: "inferred" })).toBe(false)
  expect(validate({ ...unknown, alignment: "observed", offsetToCallStartSeconds: 0 })).toBe(true)
  expect(validate({ ...unknown, alignment: "inferred", offsetToCallStartSeconds: -0.25 })).toBe(true)
  for (const example of load().components.schemas.Clock.examples) expect(validate(example)).toBe(true)
  const incomplete: Partial<typeof unknown> = { ...unknown }
  delete incomplete.origin
  expect(validate(incomplete)).toBe(false)
})

test("a position bound to another record's clock is rejected", () => {
  const spec = load()
  spec.components.schemas.Span.properties.startMs["x-clock"] = "#/components/schemas/Turn/properties/clockId"
  expect(checkSpec(spec).errors.join("\n")).toMatch(/Span\/properties\/startMs: clock reference must name its own record's clockId/)
  expect(checkSpec(load()).errors, "a recording segment on its recording clock stays valid").toEqual([])
})

test("the calls list returns summaries filtered and sorted on the server", () => {
  const spec = load()
  const list = spec.paths["/api/v1/org/{orgId}/project/{projectId}/calls"].get
  const page = spec.components.schemas.CallPage.properties.items.items.$ref
  expect(page).toBe("#/components/schemas/CallSummary")
  for (const heavy of ["spans", "turns", "events", "clocks"]) expect(spec.components.schemas.CallSummary.properties[heavy], heavy).toBeUndefined()
  const params = new Set(list.parameters.map((p: Doc) => p.name).filter(Boolean))
  for (const name of ["channel", "issues", "answerMsMin", "answerMsMax", "durationSecondsMin", "durationSecondsMax", "turnsMin", "turnsMax", "unknown", "tag", "recorded", "archived", "sort", "order"]) expect(params.has(name), name).toBeTruthy()
  const sort = list.parameters.find((p: Doc) => p.name === "sort").schema.enum
  expect(sort).toEqual(["startedAt", "durationSeconds", "turnCount", "answerMs", "issues"])
  expect(list.parameters.find((p: Doc) => p.name === "unknown").schema.items.enum.includes("answerMs"), "unknown stays filterable without matching zero").toBeTruthy()
})

test("page aggregates are server reads over a required range", () => {
  const spec = load()
  const surfaces = json("docs/api/surfaces.json")
  const reads = { getCallStats: "/overview", listIssues: "/issues", listAlertEvents: "/alerts" }
  for (const [id, route] of Object.entries(reads)) {
    const op = Object.values(spec.paths)
      .map((p) => (p as Doc).get)
      .find((g: Doc) => g?.operationId === id)
    expect(op, id).toBeTruthy()
    for (const bound of ["from", "to"]) expect(op.parameters.find((p: Doc) => p.name === bound)?.required, `${id} ${bound}`).toBe(true)
    expect(surfaces.routes[route].includes(id), `${route} reads ${id}`).toBeTruthy()
  }
  expect(surfaces.routes["/issues"].includes("listCalls"), "Issues no longer totals call pages").toBe(false)
  expect(surfaces.routes["/alerts"].includes("listCalls"), "Alerts no longer totals call pages").toBe(false)
})

test("embed tokens reach only the reads their widgets need", () => {
  const spec = load()
  const settings = spec.paths["/api/v1/org/{orgId}/project/{projectId}/settings"].get
  settings["x-embed-access"] = { ...spec.paths["/api/v1/org/{orgId}/project/{projectId}/calls"].get["x-embed-access"] }
  expect(checkCoverage(spec).errors.join("\n")).toMatch(/calls getProjectSettings: embed access beyond/)
  const calls = spec.paths["/api/v1/org/{orgId}/project/{projectId}/calls"].get
  delete calls["x-embed-access"]
  expect(checkCoverage(spec).errors.join("\n")).toMatch(/calls listCalls: widget read lacks embed access/)
  const call = spec.paths["/api/v1/org/{orgId}/project/{projectId}/calls/{callId}"].get["x-embed-access"]
  delete call.originAllowList
  call.credential = "project_key"
  const errors = checkSpec(spec).errors.join("\n")
  expect(errors).toMatch(/origin allow-list/)
  expect(errors).toMatch(/must use an embed token/)
})

test("source checker rejects epoch wall clocks and snake_case names", () => {
  const spec = load()
  const room = spec.components.schemas.MediaServer.properties.rooms.items.properties
  room.createdAt = { ...room.createdAt, type: ["integer", "null"], "x-unit": "unix_milliseconds" }
  delete room.createdAt.format
  expect(checkSpec(spec).errors.join("\n")).toMatch(/createdAt: wall-clock instants are RFC 3339/)
})

describe("Redocly contract rules", () => {
  test("accept the contract", () => {
    expect(redocly(load())).toBe("")
  })

  test("reject writes, missing errors and access, snake_case names and unattributed properties", () => {
    const spec = load()
    const list = spec.paths["/api/v1/org/{orgId}/project/{projectId}/calls"]
    delete list.get["x-access"]
    delete list.get.responses["503"]
    list.post = { operationId: "createCall", responses: { "200": { description: "Created" } } }
    const room = spec.components.schemas.MediaServer.properties.rooms.items.properties
    room.first_seen = room.firstSeenAt
    delete spec.components.schemas.Workers.properties.workers.items.properties.samples.items.properties.load["x-source"]
    const out = redocly(spec)
    for (const rule of ["reads-only", "typed-errors", "access-policy", "camel-case-properties", "property-source"]) expect(out, rule).toContain(`rule/${rule}`)
  })
})
