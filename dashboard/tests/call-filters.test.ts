import { expect, test } from "vitest"
import { createFilterQuery } from "../src/components/reui/filters/filters-query.tsx"
import type { FilterQuery } from "../src/components/reui/filters/filters-types.tsx"
import { toServerParams } from "../src/features/calls/call-filters.ts"

// The Calls chips become listCalls parameters; anything the server cannot express is named, not applied.
const rule = (field: string, operator: string, value: unknown, negated = false) => ({ id: `${field}-${operator}`, type: "rule" as const, path: [field], operator, value, negated })
const q = (rules: ReturnType<typeof rule>[], combinator: "and" | "or" = "and"): FilterQuery => ({ ...createFilterQuery([]), combinator, rules })
const labels = { answer: "Answer time (ms)", channel: "Channel" }

test("supported chips become parameters", () => {
  const { params, dropped } = toServerParams(
    q([rule("channel", "is_any_of", ["phone", "web"]), rule("answer", "between", [500, 1500]), rule("length", "empty", undefined), rule("recorded", "is", true), rule("tag", "has_any_of", ["refund"]), rule("room", "is", "call-1")]),
    labels,
  )
  expect(dropped).toEqual([])
  expect(params.getAll("channel")).toEqual(["phone", "web"])
  expect([params.get("answerMsMin"), params.get("answerMsMax")]).toEqual(["500", "1500"])
  expect(params.getAll("unknown")).toEqual(["durationSeconds"])
  expect([params.get("recorded"), params.getAll("tag"), params.get("room")]).toEqual(["true", ["refund"], "call-1"])
})

test("channel is not becomes the rest of the channels", () => {
  expect(toServerParams(q([rule("channel", "is_not", "phone")]), labels).params.getAll("channel")).toEqual(["web", "console", "local", "unknown"])
})

test("two bounds on one field keep the tighter", () => {
  const { params } = toServerParams(q([rule("turns", "gte", 2), rule("turns", "gte", 4), rule("turns", "lte", 9)]), labels)
  expect([params.get("turnsMin"), params.get("turnsMax")]).toEqual(["4", "9"])
})

test("or, and not, are named and not applied", () => {
  expect(toServerParams(q([rule("channel", "is", "phone"), rule("answer", "gte", 1)], "or"), labels).dropped[0]).toMatch(/"or"/)
  const negated = toServerParams(q([rule("answer", "gte", 1, true)]), labels)
  expect(negated.dropped).toEqual([`"not" on Answer time (ms)`])
  expect(negated.params.get("answerMsMin")).toBeNull()
})
