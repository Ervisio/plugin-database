import { useState } from 'react';
import type { DbInstance } from '../api';
import { updateLibrary, useLibrary } from '../library';
import { Button, Dialog, Icon, IconButton, Input, toast } from '../kit';
import { t } from '../i18n';
import { getSdk } from '../sdk';

export function LibraryFolders({instances,selected,onSelect,onRescan}:{instances:DbInstance[];selected:string;onSelect(id:string):void;onRescan(root:string):Promise<void>}){
 const {value,ready,error}=useLibrary();const [edit,setEdit]=useState<{id:string;name:string}|null>(null);const [busy,setBusy]=useState(false);
 const act=async(work:()=>Promise<void>)=>{setBusy(true);try{await work();}catch(error){toast.err(t('Library could not be saved'),error instanceof Error?error.message:String(error));}finally{setBusy(false);}};
 const addScanFolder=async()=>{try{const picked=await getSdk().files.pick?.({mode:'folder',title:t('Scan SQLite folder')});if(!picked)return;if(picked.path.startsWith('remote:'))throw new Error(t('Choose a folder on this server.'));await act(()=>onRescan(picked.path));}catch(error){toast.err(t('Scan SQLite folder'),error instanceof Error?error.message:String(error));}};
 return <aside className="db-library-folders" aria-label={t('Connection folders')}>
   <div className="db-library-folder-heading"><b>{t('Folders')}</b><IconButton icon="plus" label={t('New folder')} disabled={!ready||busy} onClick={()=>setEdit({id:'',name:''})}/></div>
   <button className={selected==='all'?'active':''} onClick={()=>onSelect('all')}><Icon name="db-database"/><span>{t('All connections')}</span><small>{instances.length}</small></button>
   <button className={selected==='unfiled'?'active':''} onClick={()=>onSelect('unfiled')}><Icon name="folder"/><span>{t('Unfiled')}</span><small>{instances.filter(i=>!value.placement[i.id]).length}</small></button>
   {value.folders.map(folder=><div className="db-library-folder-row" key={folder.id}><button className={selected===folder.id?'active':''} onClick={()=>onSelect(folder.id)}><Icon name="folder"/><span>{folder.name}</span><small>{instances.filter(i=>value.placement[i.id]===folder.id).length}</small></button><IconButton icon="edit" label={t('Rename folder')+': '+folder.name} onClick={()=>setEdit(folder)}/></div>)}
   <p>{t('Folders organize connections only. Database files stay where they are.')}</p>
   <Button size="sm" icon="files" disabled={!ready||busy} onClick={()=>void addScanFolder()}>{t('Find SQLite in a folder')}</Button>
   <p>{t('No automatic file scans. Open a file or search one chosen folder when needed.')}</p>
   {error&&<p role="alert" className="db-library-error">{t('Library could not be loaded')}: {error}</p>}
   {edit&&<Dialog open title={t(edit.id?'Rename folder':'New folder')} onClose={()=>{if(!busy)setEdit(null);}} dismissable={!busy} onSubmit={event=>{event.preventDefault();if(!edit.name.trim())return;void act(async()=>{const id=edit.id||crypto.randomUUID();await updateLibrary(current=>({...current,folders:edit.id?current.folders.map(f=>f.id===id?{id,name:edit.name.trim()}:f):[...current.folders,{id,name:edit.name.trim()}]}));setEdit(null);onSelect(id);});}} footer={<>{edit.id&&<Button variant="danger" disabled={busy} onClick={()=>void act(async()=>{await updateLibrary(current=>({...current,folders:current.folders.filter(f=>f.id!==edit.id),placement:Object.fromEntries(Object.entries(current.placement).filter(([,id])=>id!==edit.id))}));setEdit(null);onSelect('all');})}>{t('Remove folder')}</Button>}<Button disabled={busy} onClick={()=>setEdit(null)}>{t('Cancel')}</Button><Button type="submit" variant="primary" disabled={busy||!edit.name.trim()}>{t('Save')}</Button></>}><Input autoFocus label={t('Folder name')} maxLength={80} value={edit.name} onChange={event=>setEdit({...edit,name:event.target.value})}/><p>{t('Folders organize connections only. Database files stay where they are.')}</p></Dialog>}
 </aside>;
}

export function ConnectionFolder({instance}:{instance:DbInstance}){
 const {value,ready}=useLibrary();
 return <select className="db-library-move" aria-label={t('Folder')+': '+instance.name} value={value.placement[instance.id]||''} disabled={!ready} onChange={event=>{const id=event.target.value;void updateLibrary(current=>({...current,placement:{...current.placement,[instance.id]:id}})).catch(error=>toast.err(t('Library could not be saved'),error.message));}}><option value="">{t('Unfiled')}</option>{value.folders.map(folder=><option key={folder.id} value={folder.id}>{folder.name}</option>)}</select>;
}
