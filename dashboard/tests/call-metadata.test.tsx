// @vitest-environment jsdom
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { setCurrentSession, setMockApi } from "../src/lib/session"
import { scopeRewrite } from "../src/lib/url-state"
import { useCallMetadata } from "../src/features/call/use-call-metadata"
import { CallTags } from "../src/features/call/call-tags"

const scope={org:"default",project:"first"}
const clients:QueryClient[]=[]
const json=(status:number,body:unknown)=>new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}})
afterEach(()=>{cleanup();clients.splice(0).forEach(c=>c.clear());vi.unstubAllGlobals();setCurrentSession(null);setMockApi(false)})
function setup(role="member") {
 scopeRewrite.input({url:new URL("http://localhost/org/default/project/first/calls")})
 setMockApi(false);setCurrentSession({email:"user@example.com",organisations:[{id:"default",role}]})
 const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});clients.push(client)
 const wrapper=({children}:{children:React.ReactNode})=><QueryClientProvider client={client}>{children}</QueryClientProvider>
 return {client,wrapper}
}

test("archive waits for the server and Undo refuses a newer revision",async()=>{
 const {wrapper,client}=setup()
 let finish!:(response:Response)=>void
 let stored={tags:["demo"],archived:false,revision:3}
 const writes:unknown[]=[]
 vi.stubGlobal("fetch",async(request:Request)=>{
  if(request.method==="PUT") {
   const body=await request.json();writes.push(body)
   if(body.revision!==stored.revision)return json(409,{code:"revision_conflict",message:"Reload before saving",requestId:"test"})
   return new Promise<Response>(resolve=>{finish=resolve})
  }
  return json(200,stored)
 })
 const {result}=renderHook(()=>useCallMetadata(),{wrapper})
 let pending!:ReturnType<typeof result.current.mutateAsync>
 act(()=>{pending=result.current.mutateAsync({id:"immutable-id",archived:true})})
 await waitFor(()=>expect(writes).toEqual([{tags:["demo"],archived:true,revision:3}]))
 expect(client.getQueryData(["call-metadata",scope,"immutable-id"])).toEqual(stored)
 stored={...stored,archived:true,revision:4}
 let saved!:Awaited<typeof pending>
 await act(async()=>{finish(json(200,stored));saved=await pending})
 stored={tags:["newer"],archived:true,revision:5}
 await act(async()=>{await expect(result.current.mutateAsync({id:"immutable-id",undo:saved})).rejects.toMatchObject({status:409})})
 expect(writes[1]).toEqual({tags:["demo"],archived:false,revision:4})
 expect(stored.tags).toEqual(["newer"])
})

test("a retained action cannot write after switching projects",async()=>{
 const {wrapper}=setup();const fetch=vi.fn();vi.stubGlobal("fetch",fetch)
 const {result}=renderHook(()=>useCallMetadata(),{wrapper});const action=result.current.mutateAsync
 scopeRewrite.input({url:new URL("http://localhost/org/default/project/second/calls")})
 await act(async()=>{await expect(action({id:"same-id",archived:true})).rejects.toThrow("Project changed")})
 expect(fetch).not.toHaveBeenCalled()
})

test("Viewer sees stored tags without mutation controls",async()=>{
 const {wrapper}=setup("viewer")
 vi.stubGlobal("fetch",async(request:Request)=>json(200,request.url.endsWith("/call-tags")?{tags:["demo"]}:{tags:["demo"],archived:false,revision:1}))
 render(<CallTags callId="immutable-id"/>,{wrapper})
 expect(await screen.findByText("demo")).toBeTruthy()
 expect(screen.queryByRole("button",{name:"Remove tag demo"})).toBeNull()
 expect(screen.queryByRole("button",{name:"Tag",exact:true})).toBeNull()
})

test("denied writes remove retained metadata and do not change its revision",async()=>{
 const {wrapper,client}=setup()
 client.setQueryData(["call-metadata",scope,"immutable-id"],{tags:["private"],archived:false,revision:2})
 vi.stubGlobal("fetch",async()=>json(403,{code:"forbidden",message:"Permission revoked",requestId:"test"}))
 const {result}=renderHook(()=>useCallMetadata(),{wrapper})
 await act(async()=>{await expect(result.current.mutateAsync({id:"immutable-id",expected:{tags:["private"],archived:false,revision:2},archived:true})).rejects.toMatchObject({status:403})})
 expect(client.getQueryData(["call-metadata",scope,"immutable-id"])).toBeUndefined()
})

test.each([401,403,404])("a rejected %s refresh hides previously readable tags",async(status)=>{
 const {wrapper,client}=setup("viewer")
 let denied=false
 vi.stubGlobal("fetch",async(request:Request)=>denied?json(status,{code:"forbidden",message:"Permission revoked",requestId:"test"}):json(200,request.url.endsWith("/call-tags")?{tags:["private"]}:{tags:["private"],archived:false,revision:1}))
 render(<CallTags callId="immutable-id"/>,{wrapper})
 expect(await screen.findByText("private")).toBeTruthy()
 denied=true
 await act(async()=>{await client.invalidateQueries({queryKey:["call-metadata",scope,"immutable-id"]})})
 expect(client.getQueryState(["call-metadata",scope,"immutable-id"])?.error).toMatchObject({status})
 await waitFor(()=>expect(screen.queryByText("private")).toBeNull())
})
