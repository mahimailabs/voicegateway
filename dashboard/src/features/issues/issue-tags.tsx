import { useState } from "react"
import { Plus } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { TagChip } from "@/features/call/call-tags"
import { errorText } from "@/components/page-state"
import type { Issue } from "./issues-model"
import { useIssueMetadata } from "./issue-state"
export function IssueTags({issue}:{issue:Issue}) {
 const edit=useIssueMetadata()
 const [open,setOpen]=useState(false),[query,setQuery]=useState("")
 const fresh=query.trim().toLowerCase(),mine=issue.metadata.tags
 const valid=fresh.length>0&&Array.from(fresh).length<=64&&!/\p{Cc}/u.test(fresh)&&mine.length<32&&!mine.includes(fresh)
 const apply=async(tags:string[])=>{
  try {
   const saved=await edit.mutateAsync({id:issue.id,before:issue.metadata,tags})
   setQuery("");setOpen(false)
   toast("Issue tags saved",{action:{label:"Undo",onClick:()=>{void edit.mutateAsync({id:issue.id,before:saved.saved,undo:saved}).catch(e=>toast.error(errorText(e)))}}})
  } catch { /* Retain the entered tag and show the server error. */ }
 }
 return <div className="flex flex-wrap items-center gap-1.5 py-2">
  {mine.map(tag=><TagChip key={tag} tag={tag} onRemove={edit.canEdit&&!edit.isPending?()=>void apply(mine.filter(t=>t!==tag)):undefined}/>)}
  {edit.canEdit&&<Popover open={open} onOpenChange={setOpen}>
   <PopoverTrigger asChild><Button variant="ghost" size="sm" disabled={edit.isPending}><Plus/>Add tag</Button></PopoverTrigger>
   <PopoverContent align="start" className="w-60 p-0"><Command>
    <CommandInput placeholder="Create a tag" value={query} onValueChange={setQuery} disabled={edit.isPending}/>
    <CommandList><CommandEmpty>Enter a tag.</CommandEmpty>{fresh&&<CommandGroup><CommandItem disabled={!valid||edit.isPending} value={fresh} onSelect={()=>void apply([...mine,fresh])}>Create "{fresh}"</CommandItem></CommandGroup>}</CommandList>
   </Command></PopoverContent>
  </Popover>}
  {edit.isError&&<p role="alert" className="text-xs text-destructive">{errorText(edit.error)}</p>}
 </div>
}
