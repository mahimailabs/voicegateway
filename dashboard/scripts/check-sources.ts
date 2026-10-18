// Contract rules that need the evidence files or the dashboard code. Redocly checks the rest
// (redocly.yaml): operation tags, typed errors, an x-access policy, camelCase names and x-source on
// every property. No library resolves our evidence pointers, clock bindings, access scopes or
// page-to-endpoint coverage, so those are built here.
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { parse } from "yaml"
import ts from "typescript"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..")
// The spec and evidence files are plain JSON data; every field is checked before it is trusted.
type Doc = Record<string, any>
type Found = { found: true; value: unknown } | { found: false }
type Reader = { file: string; fixtures: string[]; assets: string[]; browserStorage: boolean }
type Declared = Reader & { operations?: string[]; exclusion?: string; assetNotes?: Record<string, string> }
type Surfaces = { routes?: Record<string, string[]>; shell?: string[]; widgets?: string[]; embedReads?: Record<string, string[]> }

const kinds = new Set(["livekit_api", "webhook", "agent_upload", "agent_metrics", "computed", "configuration"])

function pointerValue(value: unknown, pointer: unknown): Found {
  if (pointer === "") return { found: true, value }
  if (typeof pointer !== "string" || !pointer.startsWith("/")) return { found: false }
  for (const key of pointer
    .slice(1)
    .split("/")
    .map((k) => k.replaceAll("~1", "/").replaceAll("~0", "~"))) {
    if (value === null || typeof value !== "object" || !Object.hasOwn(value, key)) return { found: false }
    value = (value as Doc)[key]
  }
  return { found: true, value }
}

export function checkSpec(spec: Doc, repo = root) {
  const errors: string[] = []
  let properties = 0
  const documents = new Map<string, unknown>()
  const fail = (where: string, message: string) => errors.push(`${where}: ${message}`)
  function attribution(schema: Doc, where: string) {
    const source = schema["x-source"]
    if (!source || typeof source !== "object" || Array.isArray(source)) {
      fail(where, "missing structured x-source")
      return
    }
    if (!kinds.has(source.kind)) fail(where, "unknown x-source kind")
    if (typeof source.detail !== "string" || !source.detail.trim()) fail(where, "missing source explanation")
    if (typeof source.file !== "string" || !source.file.trim() || path.isAbsolute(source.file) || source.file.split("/").includes("..")) {
      fail(where, "source.file must name a repository file")
      return
    }
    const file = path.join(repo, source.file)
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
      fail(where, `source evidence does not exist: ${source.file}`)
      return
    }
    if (source.pointer !== null) {
      if (!source.file.endsWith(".json")) {
        fail(where, "JSON pointer must refer to a JSON evidence file")
        return
      }
      if (!documents.has(file)) documents.set(file, JSON.parse(fs.readFileSync(file, "utf8")))
      const evidence = pointerValue(documents.get(file), source.pointer)
      if (!evidence.found) fail(where, "source JSON pointer does not resolve")
      else if (schema.const !== undefined && evidence.value !== schema.const) fail(where, "schema constant contradicts its captured evidence")
    } else if (!["computed", "configuration"].includes(source.kind)) {
      fail(where, "captured sources must identify a concrete JSON evidence pointer")
    }
  }
  // Where each schema is used, so a clock binding can be checked against the records that contain it.
  const refSites = new Map<string, string[]>()
  ;(function collect(node: unknown, where: string) {
    if (node === null || typeof node !== "object") return
    if (typeof (node as Doc).$ref === "string") refSites.set((node as Doc).$ref, [...(refSites.get((node as Doc).$ref) ?? []), where])
    for (const [key, child] of Object.entries(node)) collect(child, `${where}/${key}`)
  })(spec, "#")
  function walk(node: Doc | null, where: string) {
    if (node === null || typeof node !== "object") return
    if (node.$ref) {
      if (!node.$ref.startsWith("#/")) fail(where, "external references bypass source coverage")
      else if (!pointerValue(spec, node.$ref.slice(1)).found) fail(where, "unresolved schema reference")
    }
    for (const [key, child] of Object.entries<Doc>(node.properties ?? {})) {
      properties++
      const location = `${where}/properties/${key}`
      attribution(child, location)
      if (/^unix_/.test(String(child["x-unit"]))) fail(location, "wall-clock instants are RFC 3339 date-time strings, not epoch numbers")
      const types: unknown[] = Array.isArray(child.type) ? child.type : [child.type]
      if (types.some((t) => t === "integer" || t === "number")) {
        if (typeof child["x-unit"] !== "string" || !child["x-unit"].trim()) fail(location, "numeric property lacks a unit")
        if (!types.includes("null") && !child["x-configuration"]) fail(location, "measurement must allow null")
      }
      if (types.includes("null") && !child["x-null-reason"] && !child.description) fail(location, "nullable property lacks an absence explanation")
      if (child.default === 0) fail(location, "a missing value cannot default to zero")
      if (String(child["x-unit"]).startsWith("relative_")) {
        const binding = child["x-clock"]
        const target: Found = typeof binding === "string" && binding.startsWith("#/") && binding.endsWith("/properties/clockId") ? pointerValue(spec, binding.slice(1)) : { found: false }
        if (!target.found || (target.value as Doc | null)?.type !== "string") fail(location, "relative position lacks a resolvable clock reference")
        else {
          const ownerPointer = binding.slice(0, binding.lastIndexOf("/properties/"))
          const ownerFound = pointerValue(spec, ownerPointer.slice(1))
          const owner = ownerFound.found ? (ownerFound.value as Doc | null) : null
          if (!owner?.required?.includes("clockId")) fail(location, "clock reference must be required on its owner")
          // A position is on its own record's clock, or on the clock of the record that contains it everywhere
          // it is used (a recording segment on its recording). Never on a sibling record's clock.
          const sites = refSites.get(where) ?? []
          const contained = sites.length > 0 && sites.every((site) => site.startsWith(`${ownerPointer}/`))
          if (ownerPointer !== where && !contained) fail(location, "clock reference must name its own record's clockId")
        }
      }
    }
    for (const [key, child] of Object.entries(node)) {
      if (!key.startsWith("x-") && key !== "examples" && key !== "example") walk(child, `${where}/${key}`)
    }
  }
  walk(spec, "#")
  if (!properties) fail("#", "no schema properties checked")
  for (const [name, item] of Object.entries(spec.paths ?? {})) {
    for (const method of ["get", "post", "put", "patch", "delete"]) {
      const operation = (item as Doc)[method]
      if (!operation) continue
      if (operation["x-access"]?.principal !== "organisation_member") fail(name, "missing mandatory authorisation policy")
      const expectedTag = method === "get" ? "Console reads" : "Console writes"
      if (!(operation.tags ?? []).includes(expectedTag)) fail(name, `a ${method} must be tagged ${expectedTag}`)
      const expectedScope = name.includes("{projectId}") ? "project" : name.includes("{orgId}") ? "organisation" : "principal"
      if (operation["x-access"]?.scope !== expectedScope) fail(name, "access scope does not match the resource boundary")
      if (!["Viewer", "Member", "Admin"].includes(operation["x-access"]?.minimumRole)) fail(name, "missing supported organisation role")
      if (expectedScope === "project" && operation["x-access"]?.projectIsolation !== "required") fail(name, "project isolation is mandatory")
      const embed = operation["x-embed-access"]
      if (embed !== undefined) {
        if (expectedScope !== "project") fail(name, "embed access is limited to one project")
        if (embed?.credential !== "embed_token") fail(name, "embed access must use an embed token")
        if (!Array.isArray(embed?.widgets) || !embed.widgets.length) fail(name, "embed access must name its widgets")
        if (embed?.projectIsolation !== "required") fail(name, "embed access requires project isolation")
        if (embed?.originAllowList !== "required") fail(name, "embed access requires an origin allow-list")
        if (embed?.readOnly !== true) fail(name, "embed access must be read-only")
      }
    }
  }
  return { errors, properties }
}

export function checkCoverage(spec: Doc, repo = root, inventory?: Declared[], surfaces?: Surfaces) {
  const errors: string[] = []
  inventory ??= JSON.parse(fs.readFileSync(path.join(repo, "docs/api/readers.json"), "utf8")) as Declared[]
  surfaces ??= JSON.parse(fs.readFileSync(path.join(repo, "docs/api/surfaces.json"), "utf8")) as Surfaces
  const paths = Object.values((spec.paths ?? {}) as Record<string, Doc>)
  const operationIds = new Set(paths.flatMap((p) => ["get", "post", "put", "patch", "delete"].flatMap((m) => (p[m] ? [p[m].operationId as string] : []))))
  const actual: Reader[] = []
  function visit(directory: string) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const full = path.join(directory, entry.name)
      if (entry.isDirectory()) {
        visit(full)
        continue
      }
      if (!/\.tsx?$/.test(entry.name)) continue
      const text = fs.readFileSync(full, "utf8")
      const ast = ts.createSourceFile(full, text, ts.ScriptTarget.Latest, true)
      const fixtures: string[] = []
      const assets: string[] = []
      function inspect(node: ts.Node) {
        // Static imports and import() both count: a lazy fixture is still a fixture this file reads.
        const specifier = ts.isImportDeclaration(node) ? node.moduleSpecifier : ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword ? node.arguments[0] : undefined
        if (specifier && ts.isStringLiteralLike(specifier)) {
          const name = specifier.text
          const target = name.startsWith("@/") ? path.join(repo, "dashboard/src", name.slice(2)) : name.startsWith(".") ? path.resolve(path.dirname(full), name) : null
          if (target?.startsWith(path.join(repo, "dashboard/src/mocks/"))) fixtures.push(path.basename(target))
        }
        if (ts.isCallExpression(node) && (node.expression.getText(ast) === "fetch" || (ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === "fetch"))) {
          assets.push(node.arguments[0] && ts.isStringLiteralLike(node.arguments[0]) ? node.arguments[0].text : "<dynamic>")
        }
        ts.forEachChild(node, inspect)
      }
      inspect(ast)
      const browserStorage = /\blocalStorage\s*[.[]/.test(text)
      if (fixtures.length || assets.length || browserStorage) actual.push({ file: path.relative(repo, full), fixtures, assets, browserStorage })
    }
  }
  visit(path.join(repo, "dashboard/src"))
  for (const reader of actual) {
    const matches = inventory.filter((entry) => entry.file === reader.file)
    if (matches.length !== 1) {
      errors.push(`${reader.file}: requires exactly one read inventory entry`)
      continue
    }
    const declared = matches[0]
    for (const key of ["fixtures", "assets", "browserStorage"] as const) {
      const normalise = (value: unknown) => (Array.isArray(value) ? [...value].sort() : value)
      if (JSON.stringify(normalise(reader[key])) !== JSON.stringify(normalise(declared[key]))) errors.push(`${reader.file}: ${key} inventory drift`)
    }
    if (!declared.operations?.length && !declared.exclusion && !reader.assets.length) errors.push(`${reader.file}: data reader has no endpoint or exclusion`)
    for (const asset of reader.assets) if (!declared.assetNotes?.[asset]?.trim()) errors.push(`${reader.file}: unexplained fetch ${asset}`)
    for (const id of declared.operations ?? []) if (!operationIds.has(id)) errors.push(`${reader.file}: unknown operation ${id}`)
  }
  for (const declared of inventory) if (!actual.some((reader) => reader.file === declared.file)) errors.push(`${declared.file}: stale reader inventory`)
  const router = ts.createSourceFile("router.tsx", fs.readFileSync(path.join(repo, "dashboard/src/router.tsx"), "utf8"), ts.ScriptTarget.Latest, true)
  const routes: string[] = []
  function findRoutes(node: ts.Node) {
    if (ts.isPropertyAssignment(node) && node.name.getText(router) === "path" && ts.isStringLiteral(node.initializer)) routes.push(node.initializer.text)
    ts.forEachChild(node, findRoutes)
  }
  findRoutes(router)
  for (const route of routes) {
    if (!surfaces.routes?.[route]?.length) errors.push(`${route}: missing page-to-endpoint coverage`)
    for (const id of surfaces.routes?.[route] ?? []) if (!operationIds.has(id)) errors.push(`${route}: unknown operation ${id}`)
  }
  for (const route of Object.keys(surfaces.routes ?? {})) if (!routes.includes(route)) errors.push(`${route}: stale route coverage`)
  for (const id of surfaces.shell ?? []) if (!operationIds.has(id)) errors.push(`shell: unknown operation ${id}`)
  const widgetText = fs.readFileSync(path.join(repo, "dashboard/src/features/embed/widgets.ts"), "utf8")
  const widgets = [...widgetText.matchAll(/\bid:\s*["']([^"']+)["']/g)].map((m) => m[1])
  if (JSON.stringify([...widgets].sort()) !== JSON.stringify([...(surfaces.widgets ?? [])].sort())) errors.push("widget inventory drift")
  const embedReads = surfaces.embedReads ?? {}
  if (JSON.stringify(Object.keys(embedReads).sort()) !== JSON.stringify([...(surfaces.widgets ?? [])].sort())) errors.push("every widget needs its embed reads")
  const granted = new Map<string, boolean>()
  for (const item of paths) {
    if (!item.get) continue
    for (const widget of (item.get["x-embed-access"]?.widgets ?? []) as string[]) granted.set(`${widget} ${item.get.operationId}`, true)
  }
  const declared = new Set(Object.entries(embedReads).flatMap(([widget, ids]) => ids.map((id) => `${widget} ${id}`)))
  for (const key of declared) if (!granted.has(key)) errors.push(`embed ${key}: widget read lacks embed access`)
  for (const key of granted.keys()) if (!declared.has(key)) errors.push(`embed ${key}: embed access beyond the widget's reads`)
  return { errors, readers: actual.length, routes: routes.length }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const spec = parse(fs.readFileSync(path.join(root, "docs/api/openapi.yaml"), "utf8")) as Doc
    const result = checkSpec(spec)
    const coverage = checkCoverage(spec)
    result.errors.push(...coverage.errors)
    if (result.errors.length) {
      console.error(result.errors.join("\n"))
      process.exitCode = 1
    } else console.log(`${result.properties}/${result.properties} schema properties have resolvable source attribution (100%); ${coverage.readers} data readers and ${coverage.routes} routes inventoried.`)
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error))
    process.exitCode = 1
  }
}
