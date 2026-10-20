import type { CallMetadata } from "@/lib/api-types"

// Development-only simulated server storage. The product UI never reads this storage.
const key="vg.preview.call-metadata"
function read():Record<string,CallMetadata> { try{return JSON.parse(localStorage.getItem(key)??"{}")}catch{return {}} }
export function metadataOf(scope:string,id:string):CallMetadata {return read()[`${scope}/${id}`]??{tags:[],archived:false,revision:0}}
export function saveMetadata(scope:string,id:string,value:CallMetadata) {localStorage.setItem(key,JSON.stringify({...read(),[`${scope}/${id}`]:value}))}
