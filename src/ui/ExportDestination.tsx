import { useEffect, useState, useSyncExternalStore } from 'react';
import { DestinationFields, type Destination, type DestinationConnection } from '@ervisio/plugin-sdk/destinations';
import { getSdk } from '../sdk';
import { Button, Checkbox, Dialog, Input } from '../kit';
import { t } from '../i18n';

type Request={name:string;body:string;mime:string;resolve(ok:boolean):void};
let pending:Request|null=null;
const listeners=new Set<()=>void>();
const announce=()=>listeners.forEach(fn=>fn());
export function exportFile(name:string,body:string,mime:string):Promise<boolean>{
  pending?.resolve(false);
  return new Promise(resolve=>{pending={name,body,mime,resolve};announce();});
}
async function helper<T>(command:string,token?:string):Promise<T>{
  const result=await getSdk().api.exec(command,token?[token]:[]);
  if(result.truncated)throw new Error(t('Export failed'));
  let response;try{response=JSON.parse(result.stdout);}catch{throw new Error(t('The export helper returned an invalid response.'));}
  if(!response.ok||result.exitCode!==0)throw new Error(response.error?.message||t('Export failed'));
  return response.result;
}
function id(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');}

export function ExportDestinationHost(){
  const request=useSyncExternalStore(fn=>{listeners.add(fn);return()=>{listeners.delete(fn);};},()=>pending);
  useEffect(()=>()=>{pending?.resolve(false);pending=null;announce();},[]);
  if(!request)return null;
  return <ExportDialog key={request.name} request={request} close={ok=>{request.resolve(ok);if(pending===request){pending=null;announce();}}}/>;
}
function ExportDialog({request,close}:{request:Request;close(ok:boolean):void}){
  const [where,setWhere]=useState<'browser'|'destination'>('browser');
  const [name,setName]=useState(request.name.replace(/\.(\w+)$/,`-${new Date().toISOString().replace(/[:.]/g,'-')}.$1`));
  const [destination,setDestination]=useState<Destination>({kind:'local',connection:'',path:''});
  const [connections,setConnections]=useState<DestinationConnection[]>([]);
  const [remember,setRemember]=useState(false);const [revision,setRevision]=useState('');
  const [busy,setBusy]=useState(false);const [loading,setLoading]=useState(false);const [error,setError]=useState('');
  const linux=getSdk().platform!=='windows';
  useEffect(()=>{let alive=true;
    if(linux)void helper<{connections:DestinationConnection[]}>('db-export-remotes').then(result=>{if(alive)setConnections(result.connections);}).catch(()=>{});
    const data=getSdk().data;
    if(data){setLoading(true);void data.read('export-destination.json').then(saved=>{if(alive){setDestination(JSON.parse(saved.text));setRevision(saved.revision);setRemember(true);}}).catch(e=>{if(alive&&e.code!=='not_found')setError(t('The saved destination could not be loaded.'));}).finally(()=>{if(alive)setLoading(false);});}
    return()=>{alive=false;};
  },[linux]);
  const save=async()=>{
    setBusy(true);setError('');const sdk=getSdk();const token=id();const parts:string[]=[];
    const input=`~/.config/ervisio/plugins/backup/inbox/${token}.json`;
    try{
      if(!name.trim()||/[\\/\x00-\x1f]/.test(name))throw new Error(t('Choose a filename without a directory.'));
      if(where==='browser'){await sdk.saveFile(name,request.body,request.mime);close(true);return;}
      const bytes=new TextEncoder().encode(request.body).length;if(!bytes||bytes>64*1024**2)throw new Error(t('Exports must be between 1 byte and 64 MiB.'));
      await helper('db-export-status');
      await sdk.files.mkdir('~/.config/ervisio/plugins/database/exports');
      let offset=0;
      while(offset<request.body.length){let end=Math.min(request.body.length,offset+1_000_000);if(end<request.body.length){const last=request.body.charCodeAt(end-1);if(last>=0xd800&&last<=0xdbff)end--;}
        const path=`~/.config/ervisio/plugins/database/exports/${token}-${String(parts.length).padStart(4,'0')}.part`;parts.push(path);await sdk.files.write(path,request.body.slice(offset,end));offset=end;}
      await sdk.files.write(input,JSON.stringify({destination,name:name.trim(),parts:parts.length,bytes}));
      await helper('db-export-deliver',token);
      if(remember&&sdk.data){const saved=await sdk.data.write('export-destination.json',JSON.stringify(destination),revision);setRevision(saved.revision);}
      else if(revision&&sdk.data)await sdk.data.remove('export-destination.json',revision);
      close(true);
    }catch(e){setError(e instanceof Error?e.message:t('Export failed'));}
    finally{await Promise.all([...parts,input].map(path=>sdk.files.remove(path).catch(()=>{})));setBusy(false);}
  };
  return <Dialog open title={t('Export destination')} size="lg" dismissable={!busy} onClose={()=>{if(!busy)close(false);}} onSubmit={event=>{event.preventDefault();void save();}} footer={<><Button disabled={busy} onClick={()=>close(false)}>{t('Cancel')}</Button><Button type="submit" variant="primary" disabled={busy||loading} loading={busy}>{t('Export')}</Button></>}>
    <div style={{display:'grid',gap:16}}>
      {error&&<p role="alert" style={{color:'var(--err)'}}>{error}</p>}
      <label>{t('Save to')}<select value={where} disabled={busy} onChange={e=>setWhere(e.target.value as typeof where)}><option value="browser">{t('Download in browser')}</option>{linux&&<option value="destination">{t('Server folder, network or cloud')}</option>}</select></label>
      <Input label={t('Filename')} value={name} disabled={busy} onChange={e=>setName(e.target.value)} required />
      {where==='destination'&&<><DestinationFields value={destination} onChange={setDestination} connections={connections} disabled={busy}/><Checkbox checked={remember} disabled={busy||loading} label={t('Remember this destination and credentials for my account')} onChange={setRemember}/><p>{t('Exports never delete older files. Existing files are not replaced.')}</p></>}
    </div>
  </Dialog>;
}
