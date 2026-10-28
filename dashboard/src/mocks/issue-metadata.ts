import type { IssueMetadata } from "@/lib/api-types"
// Explicit preview adapter only; production decisions are persisted by the scoped API.
const key = "vg.preview.issue-metadata"
function read(): Record<string, IssueMetadata> { try {return JSON.parse(localStorage.getItem(key) ?? "{}")} catch {return {}} }
export function issueMetadataOf(scope: string, id: string): IssueMetadata {return read()[`${scope}/${id}`] ?? {issueId:id,state:"open",regressed:false,decidedAt:null,evidenceRevision:1,reviewedEvidenceRevision:0,revision:0,tags:[],archived:false}}
export function saveIssueMetadata(scope: string,id: string,value: IssueMetadata) {localStorage.setItem(key,JSON.stringify({...read(),[`${scope}/${id}`]:value}))}
