import { readLocal, writeLocal } from "@/lib/local-store"

// "Set up later" is this viewer's choice for one organisation (no project yet) or one project: the
// console stops opening the setup journey there. It lives in this browser only; the journey stays
// reachable at /setup.
const KEY = "vg.setup-later"
const id = (org: string, project: string | null) => `${org}/${project ?? ""}`
const saved = () => {
  const v = readLocal(KEY)
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []
}
export const setUpLater = (org: string, project: string | null) => writeLocal(KEY, [...new Set([...saved(), id(org, project)])])
export const choseLater = (org: string, project: string | null) => saved().includes(id(org, project))
