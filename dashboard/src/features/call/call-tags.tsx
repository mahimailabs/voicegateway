import { useState } from "react"
import { Plus, Tag, X } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

import { useQuery } from "@tanstack/react-query"
import { ApiError, metadataQuery, callTagsQuery } from "@/lib/api"
import { errorText } from "@/components/page-state"
import { useCallMetadata } from "./use-call-metadata"

export function TagChip({ tag, onRemove }: { tag: string; onRemove?: () => void }) {
  return (
    <span className="inline-flex h-6 items-center gap-1 rounded-full bg-muted pr-1.5 pl-2 text-[12px] text-muted-foreground">
      <Tag size={11} />
      {tag}
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label={`Remove tag ${tag}`} className="ml-0.5 rounded-full p-0.5 hover:bg-foreground/10 hover:text-foreground">
          <X size={10} weight="bold" />
        </button>
      )}
    </span>
  )
}

// The call's tags with a picker that reuses existing tags or creates a new one.
export function CallTags({callId,disabled=false}:{callId:string;disabled?:boolean}) {
 const edit=useCallMetadata()
 const read=useQuery({...metadataQuery(callId,edit.selected),enabled:!disabled})
 const catalog=useQuery({...callTagsQuery(edit.selected),enabled:!disabled})
 const denied=read.error instanceof ApiError && [401,403,404].includes(read.error.status)
 const mine=denied?[]:read.data?.tags ?? []
 const all=catalog.isError?[]:catalog.data?.tags ?? []
 const [open,setOpen]=useState(false)
 const [q,setQ]=useState("")
 const fresh=q.trim().toLowerCase()
 const valid=fresh.length>0 && Array.from(fresh).length<=64 && !/\p{Cc}/u.test(fresh) && mine.length<32
 const apply=async(next:string[],label:string) => {
  if (!read.data) return
  try {
   const result=await edit.mutateAsync({id:callId,tags:next,expected:read.data})
   setQ("");setOpen(false)
   toast(label,{action:{label:"Undo",onClick:()=>{void edit.mutateAsync({id:callId,undo:result}).catch(error=>toast.error(errorText(error)))}}})
  } catch { /* The inline error retains the input and menu for retry. */ }
 }
 return <div className="flex flex-wrap items-center gap-1.5">
  {mine.map(t=><TagChip key={t} tag={t} onRemove={edit.canEdit && !edit.isPending ? ()=>void apply(mine.filter(x=>x!==t),`Removed tag "${t}"`) : undefined}/>)}
  {!disabled && read.isError && <Button size="sm" variant="ghost" onClick={()=>void read.refetch()}>Retry tags</Button>}
  {!disabled && edit.canEdit && read.data && !denied && <Popover open={open} onOpenChange={setOpen}>
   <PopoverTrigger asChild><Button disabled={edit.isPending} variant="ghost" size="sm" className="h-6 gap-1 rounded-full px-2 text-[12px] text-muted-foreground"><Plus size={11} weight="bold"/>{mine.length?"Tag":"Add tag"}</Button></PopoverTrigger>
   <PopoverContent align="start" className="w-60 p-0"><Command>
    <CommandInput disabled={edit.isPending} placeholder="Find or create a tag" value={q} onValueChange={setQ}/>
    <CommandList><CommandEmpty>No matching tags.</CommandEmpty>
     {all.filter(t=>!mine.includes(t)).length>0 && <CommandGroup heading="Tags">{all.filter(t=>!mine.includes(t)).map(t=><CommandItem key={t} value={t} disabled={edit.isPending || mine.length>=32} onSelect={()=>void apply([...mine,t],`Added tag "${t}"`)}><Tag size={13}/>{t}</CommandItem>)}</CommandGroup>}
     {fresh && !all.includes(fresh) && !mine.includes(fresh) && <CommandGroup><CommandItem value={`create ${fresh}`} disabled={!valid || edit.isPending} onSelect={()=>void apply([...mine,fresh],`Added tag "${fresh}"`)}><Plus size={13}/>Create "{fresh}"</CommandItem></CommandGroup>}
    </CommandList>
   </Command>
   {catalog.isError && <p className="px-3 py-2 text-xs text-muted-foreground">Existing tags could not be loaded.</p>}
   {fresh && !valid && <p className="px-3 py-2 text-xs text-destructive">Up to 32 tags, each 1–64 characters without control characters.</p>}
   </PopoverContent>
  </Popover>}
  {edit.isError && <p role="alert" className="text-xs text-destructive">{errorText(edit.error)} <Button variant="link" size="sm" onClick={()=>{edit.reset();void read.refetch()}}>Reload tags</Button></p>}
 </div>
}
