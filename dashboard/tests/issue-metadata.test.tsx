// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { setCurrentSession, setMockApi } from "../src/lib/session"
import { scopeRewrite } from "../src/lib/url-state"
import { useIssueMetadata } from "../src/features/issues/issue-state"
import type { IssueMetadata } from "../src/lib/api-types"
const scope={org:"default",project:"first"}
const before:IssueMetadata={issueId:"slow",tags:[],archived:false,revision:2,evidenceRevision:7,reviewedEvidenceRevision:4,state:"open",regressed:true,decidedAt:null}
const clients:QueryClient[]=[]
const json=(status:number,body:unknown)=>new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}})
afterEach(()=>{cleanup();clients.splice(0).forEach(c=>c.clear());vi.unstubAllGlobals();setCurrentSession(null);setMockApi(false)})
function setup(role="member") {
 scopeRewrite.input({url:new URL("http://localhost/org/default/project/first/issues")})
 setMockApi(false);setCurrentSession({email:"user@example.com",organisations:[{id:"default",role}]})
 const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});clients.push(client)
 const wrapper=({children}:{children:React.ReactNode})=><QueryClientProvider client={client}>{children}</QueryClientProvider>
 return {client,wrapper}
}
test("resolving acknowledges exactly the displayed evidence; Undo refuses newer evidence",async()=>{
 const {wrapper}=setup();const writes:unknown[]=[]
 let stored={...before}
 vi.stubGlobal("fetch",async(request:Request)=>{
  const body=await request.json();writes.push(body)
  if(body.revision!==stored.revision || body.evidenceRevision!==stored.evidenceRevision)return json(409,{code:"revision_conflict",message:"Reload",requestId:"test"})
  stored={...stored,...body,regressed:false,revision:stored.revision+1,reviewedEvidenceRevision:body.evidenceRevision}
  return json(200,stored)
 })
 const {result}=renderHook(()=>useIssueMetadata(),{wrapper})
 let edit!:Awaited<ReturnType<typeof result.current.mutateAsync>>
 await act(async()=>{edit=await result.current.mutateAsync({id:"slow",before,state:"resolved"})})
 expect(writes[0]).toEqual({tags:[],archived:false,revision:2,evidenceRevision:7,state:"resolved"})
 stored={...stored,evidenceRevision:8}
 await act(async()=>{await expect(result.current.mutateAsync({id:"slow",before:edit.saved,undo:edit})).rejects.toMatchObject({status:409})})
 expect(stored.evidenceRevision).toBe(8)
})
test("archive alone never acknowledges a regression",async()=>{
 const {wrapper}=setup();let sent:unknown
 vi.stubGlobal("fetch",async(request:Request)=>{sent=await request.json();return json(200,{...before,archived:true,revision:3})})
 const {result}=renderHook(()=>useIssueMetadata(),{wrapper})
 let edit!:Awaited<ReturnType<typeof result.current.mutateAsync>>
 await act(async()=>{edit=await result.current.mutateAsync({id:"slow",before,archived:true})})
 expect(sent).toEqual({tags:[],archived:true,revision:2,evidenceRevision:7})
 await act(async()=>{await result.current.mutateAsync({id:"slow",before:edit.saved,undo:edit})})
 expect(sent).toEqual({tags:[],archived:false,revision:3,evidenceRevision:7})
})
test("retained issue action cannot write after project switch",async()=>{
 const {wrapper}=setup();const fetch=vi.fn();vi.stubGlobal("fetch",fetch)
 const {result}=renderHook(()=>useIssueMetadata(),{wrapper});const action=result.current.mutateAsync
 scopeRewrite.input({url:new URL("http://localhost/org/default/project/second/issues")})
 await act(async()=>{await expect(action({id:"slow",before,state:"resolved"})).rejects.toThrow("Project changed")})
 expect(fetch).not.toHaveBeenCalled()
})
test("Viewer cannot mutate issue metadata",async()=>{
 const {wrapper}=setup("viewer");const fetch=vi.fn();vi.stubGlobal("fetch",fetch)
 const {result}=renderHook(()=>useIssueMetadata(),{wrapper})
 expect(result.current.canEdit).toBe(false)
 await act(async()=>{await expect(result.current.mutateAsync({id:"slow",before,state:"ignored"})).rejects.toThrow("Member or Admin")})
 expect(fetch).not.toHaveBeenCalled()
})
test.each([401,403,404])("denied %s writes clear retained issue evidence",async(status)=>{
 const {wrapper,client}=setup();client.setQueryData(["issues",scope],{items:[{metadata:before}]})
 vi.stubGlobal("fetch",async()=>json(status,{code:"forbidden",message:"Denied",requestId:"test"}))
 const {result}=renderHook(()=>useIssueMetadata(),{wrapper})
 await act(async()=>{await expect(result.current.mutateAsync({id:"slow",before,state:"resolved"})).rejects.toMatchObject({status})})
 expect(client.getQueryData(["issues",scope])).toBeUndefined()
})
