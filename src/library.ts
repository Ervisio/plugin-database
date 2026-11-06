import { useEffect, useSyncExternalStore } from 'react';
import type { DbInstance } from './api';
import { getSdk } from './sdk';

export interface Library { folders: {id:string;name:string}[]; placement:Record<string,string>; saved:DbInstance[]; roots:string[] }
export function mergeConnections(detected:DbInstance[],saved:DbInstance[]):DbInstance[]{
 const items=new Map(detected.map(instance=>[instance.id,instance]));
 for(const instance of saved){const current=items.get(instance.id);items.set(instance.id,current?{...instance,...current,name:instance.name,user:instance.user??current.user,password:instance.password??current.password,tls:instance.tls??current.tls,ca_file:instance.ca_file??current.ca_file,auth_db:instance.auth_db??current.auth_db,unix_socket:instance.unix_socket??current.unix_socket}:instance);}
 return [...items.values()];
}
const empty=():Library=>({folders:[],placement:{},saved:[],roots:[]});
let state={value:empty(),ready:false,error:''};let revision='';let loading:Promise<Library>|undefined;
let writes:Promise<unknown>=Promise.resolve();
const subscribers=new Set<()=>void>();const notify=()=>subscribers.forEach(fn=>fn());
const key='connection-library.json';
export async function ensureLibrary():Promise<Library>{
 if(state.ready)return state.value;
 if(!loading)loading=(async()=>{
  const sdk=getSdk();if(!sdk.data)throw new Error('Update Ervisio to save your database library.');
  try{
   const doc=await sdk.data.read(key);const value=JSON.parse(doc.text) as Library;
   if(!Array.isArray(value.folders)||!Array.isArray(value.saved)||!Array.isArray(value.roots)||!value.placement||typeof value.placement!=='object'||Array.isArray(value.placement)||value.folders.length>100||value.saved.length>500||value.roots.length>12)throw new Error('Invalid saved database library.');
   if(value.folders.some(f=>typeof f.id!=='string'||typeof f.name!=='string')||value.saved.some(i=>!i||typeof i.id!=='string'||typeof i.name!=='string')||value.roots.some(p=>typeof p!=='string'))throw new Error('Invalid saved database library.');
   revision=doc.revision;state={value,ready:true,error:''};
  }catch(error){if((error as {code?:string}).code!=='not_found')throw error;state={value:empty(),ready:true,error:''};}
  notify();return state.value;
 })().catch(error=>{state={...state,error:error instanceof Error?error.message:'Could not load the database library.'};loading=undefined;notify();throw error;});
 return loading;
}
export function useLibrary(){const snapshot=useSyncExternalStore(fn=>{subscribers.add(fn);return()=>{subscribers.delete(fn);};},()=>state);useEffect(()=>{void ensureLibrary().catch(()=>{});},[]);return snapshot;}
export function updateLibrary(change:(value:Library)=>Library):Promise<void>{
 const run=async()=>{const before=await ensureLibrary();const next=change(structuredClone(before));const data=getSdk().data!;const result=await data.write(key,JSON.stringify(next),revision);revision=result.revision;state={value:next,ready:true,error:''};notify();};
 const task=writes.then(run,run);writes=task.catch(()=>{});return task;
}
export function rememberInstance(instance:DbInstance,credentials=false){
 const copy={...instance};if(!credentials)delete copy.password;
 return updateLibrary(value=>({...value,saved:[...value.saved.filter(i=>i.id!==copy.id),copy]}));
}
