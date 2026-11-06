import { LibraryFolders, ConnectionFolder } from '../ui/ConnectionLibrary';
import { mergeConnections, rememberInstance, updateLibrary, useLibrary } from '../library';
import { PathInput } from '@ervisio/plugin-sdk/paths';
import { useEffect, useState, type MouseEvent } from 'react';
import { Button, ConfirmDialog, Dialog, EmptyState, Icon, IconButton, Input, Select, Skeleton, StatCard, Segmented, toast } from '../kit';
import { dbApi, detectCache, invalidateInstance, type DbInstance } from '../api';
import { t } from '../i18n';
import { openExplorer } from '../router';
import { PageHeader } from '../ui/PageHeader';
import { RuntimeDrivers } from '../ui/RuntimeDrivers';
import { engineIcon } from '../ui/icons';
import { buildConnectionString, copyText } from '../utils';
import { pushRecent, useFile } from '../settings';
import { getSdk } from '../sdk';

export function InstancesView({ search }: { search: string }) {
  const det = detectCache.use([]);
  const [recent] = useFile('recent');
  const library=useLibrary();const [folder,setFolder]=useState('all');const [connection,setConnection]=useState<DbInstance|null>(null);const [openingSqlite,setOpeningSqlite]=useState(false);

  const [openInstall, setOpenInstall] = useState(false);
  const [openConnect, setOpenConnect] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<DbInstance | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const instances = det.data?.instances ?? [];
  const sqliteFiles = det.data?.sqlite_files ?? [];
  const all = mergeConnections([...instances,...sqliteFiles],library.value.saved);
  const q = search.trim().toLowerCase();
  const filtered = all.filter(i=>folder==='all'||(folder==='unfiled'?!library.value.placement[i.id]:library.value.placement[i.id]===folder)).filter((i) => !q || [i.name, i.type, i.mode, i.host, String(i.port || ''), i.path || ''].join(' ').toLowerCase().includes(q));
  const running = all.filter((i) => i.status === 'running').length;
  const recentList = recent.ids.map((id) => filtered.find((i) => i.id === id)).filter(Boolean) as DbInstance[];
  const rest = filtered.filter((i) => !recent.ids.includes(i.id));

  const open = (inst: DbInstance) => {
    if(inst.id.startsWith('custom-')&&inst.password===undefined){setConnection(inst);setOpenConnect(true);return;}
    void pushRecent(inst.id);
    openExplorer(inst);
  };

  const manage = async (inst: DbInstance, op: 'start' | 'stop' | 'restart' | 'remove') => {
    setBusyId(inst.id);
    try {
      await dbApi.manageInstance({ op, mode: inst.mode, target: inst.service || inst.container || inst.id });
      toast.ok(t('Done: {op}', { op }), inst.name);
      invalidateInstance(inst, 'all');
      await detectCache.get([], true);
    } catch (err: any) {
      toast.err(t('Failed to {op}', { op }), err.message);
    } finally {
      setBusyId(null);
    }
  };

  const copyConn = async (inst: DbInstance) => {
    const ok = await copyText(buildConnectionString(inst));
    if (ok) toast.ok(t('Connection string copied'));
    else toast.warn(t('Copy blocked'), t('The frame denied clipboard access'));
  };

  const exportList = async () => {
    try {
      const safe = all.map(({ password: _p, ...rest }) => rest);
      await getSdk().saveFile('db-engines.json', JSON.stringify(safe, null, 2), 'application/json');
      toast.ok(t('Exported'), 'db-engines.json');
    } catch (err: any) {
      toast.err(t('Export failed'), err.message);
    }
  };

  const openSqlite=async()=>{setOpeningSqlite(true);try{const selected=await getSdk().files.pick?.({mode:'file',title:t('Open SQLite database')});if(!selected)return;if(selected.path.startsWith('remote:'))throw new Error(t('Choose a file on this server.'));const instance=await dbApi.openSqlite(selected.path);await rememberInstance(instance);open(instance);}catch(error){toast.err(t('Open SQLite database'),error instanceof Error?error.message:String(error));}finally{setOpeningSqlite(false);}};
  const card = (inst: DbInstance) => {
    const up = inst.status === 'running' || (inst.engine==='sqlite'&&inst.status==='ready');
    const stop = (e: MouseEvent) => e.stopPropagation();
    return (
      <div
        key={inst.id}
        className="db-eng hue-plg"
        role="button"
        tabIndex={0}
        onClick={() => open(inst)}
        onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) { e.preventDefault(); open(inst); } }}
        aria-label={t('Open {name}', { name: inst.name })}
      >
        <div className="db-eng-h">
          <span className="db-eng-ic"><Icon name={engineIcon(inst.type)} /></span>
          <div className="db-eng-t">
            <b title={inst.name}>{inst.name}</b>
            <span>{inst.type.toUpperCase()}{inst.version ? ` ${inst.version}` : ''} · {inst.id.startsWith('custom-')?t('Saved connection'):MODE_LABEL[inst.mode]?.() ?? inst.mode}</span>
          </div>
          <span className={`db-dot ${up ? 'db-dot--ok' : 'db-dot--warn'}`} title={inst.status} />
        </div>
        {inst.port ? <div className="db-kv"><span>{t('Address')}</span><code>{inst.host || '127.0.0.1'}:{inst.port}</code></div> : null}
        {inst.path && <div className="db-kv"><span>{t('File')}</span><code>{inst.path}</code></div>}
        <div className="db-eng-f" onClick={stop}>
          {(inst.mode === 'docker' || (inst.mode === 'native' && inst.service)) && (
            <>
              {up
                ? <IconButton icon="stop" label={t('Stop')} size="sm" loading={busyId === inst.id} onClick={() => manage(inst, 'stop')} />
                : <IconButton icon="play" label={t('Start')} size="sm" loading={busyId === inst.id} onClick={() => manage(inst, 'start')} />}
              <IconButton icon="refresh" label={t('Restart')} size="sm" onClick={() => manage(inst, 'restart')} />
              {inst.mode === 'docker' && <IconButton icon="trash" label={t('Remove container')} size="sm" onClick={() => setPendingDelete(inst)} />}
            </>
          )}
          <IconButton icon="copy" label={t('Copy connection string')} size="sm" onClick={() => copyConn(inst)} />
          {inst.engine!=='sqlite'&&<IconButton icon="key" label={t('Connection settings')} size="sm" onClick={()=>{setConnection(inst);setOpenConnect(true);}}/>}
          <ConnectionFolder instance={inst}/>{library.value.saved.some(item=>item.id===inst.id)&&<IconButton icon="close" label={t('Forget connection')} onClick={()=>void updateLibrary(value=>({...value,saved:value.saved.filter(item=>item.id!==inst.id)})).catch(error=>toast.err(t('Library could not be saved'),error.message))}/>}
          <span className="db-grow" />
          <Button size="sm" variant={up ? 'primary' : 'secondary'} icon="db-open" onClick={() => open(inst)}>{t('Open')}</Button>
        </div>
      </div>
    );
  };

  return (
    <div className="db-col" style={{ gap: 16 }}>
      <PageHeader
        icon="db-database"
        title={t('Database engines')}
        subtitle={t('MySQL, MariaDB, PostgreSQL, SQLite, MongoDB and Redis on this server')}
        actions={
          <>
            <IconButton icon="refresh" label={t('Refresh')} loading={det.loading} onClick={() => void det.refresh()} />
            <IconButton icon="download" label={t('Export list (no passwords)')} onClick={exportList} />
            <Button variant="secondary" icon="link" onClick={() => {setConnection(null);setOpenConnect(true);}}>{t('Connect')}</Button>
            <Button variant="secondary" icon="files" loading={openingSqlite} onClick={()=>void openSqlite()}>{t('Open SQLite')}</Button>
            <Button variant="primary" icon="plus" onClick={() => setOpenInstall(true)}>{t('New engine')}</Button>
          </>
        }
      />

      <RuntimeDrivers />
      {det.data?.warnings?.length ? <details className="db-discovery-report"><summary>{t('Discovery report')} · {det.data.warnings.length} {t('warnings')}</summary>{det.data.warnings.map((warning,index)=><p key={index}><b>{warning.source}</b>: {warning.message}</p>)}<p>{t('Detected servers may still require credentials. Discovery does not bypass authentication.')}</p></details>:null}
      <div className="db-library-layout"><LibraryFolders instances={all} selected={folder} onSelect={setFolder} onRescan={()=>void det.refresh()}/><div className="db-library-content">
      {!det.data && det.loading ? (
        <Skeleton lines={4} />
      ) : det.error && !det.data ? (
        <EmptyState icon="alert" title={t('Detection failed')} text={det.error} action={<Button variant="primary" icon="refresh" onClick={() => void det.refresh()}>{t('Retry')}</Button>} />
      ) : (
        <>
          <div className="db-stats">
            <StatCard hue="plg" icon="db-database" label={t('Engines')} value={all.length} />
            <StatCard hue="plg" icon="play" label={t('Running')} value={running} />
            <StatCard hue="plg" icon="db-docker" label="Docker" value={instances.filter((i) => i.mode === 'docker').length} />
            <StatCard hue="plg" icon="db-sqlite" label={t('SQLite files')} value={sqliteFiles.length} />
          </div>

          {filtered.length === 0 ? (
            <EmptyState
              icon="db-database"
              title={q ? t('No engines match “{q}”', { q: search }) : folder!=='all'?t('This folder is empty'):t('No databases found')}
              text={q ? undefined : folder!=='all'?t('Move connections into this folder using the folder selector on each card.'):t('Review discovery warnings, choose scan folders, open a SQLite file or connect to a server.')}
              action={q ? undefined : <Button variant="primary" icon="plus" onClick={() => setOpenInstall(true)}>{t('New engine')}</Button>}
            />
          ) : (
            <>
              {recentList.length > 0 && (
                <>
                  <h2 className="db-section-t">{t('Recently opened')}</h2>
                  <div className="db-grid">{recentList.map(card)}</div>
                </>
              )}
              {rest.length > 0 && (
                <>
                  {recentList.length > 0 && <h2 className="db-section-t">{t('All engines')}</h2>}
                  <div className="db-grid">{rest.map(card)}</div>
                </>
              )}
            </>
          )}
        </>
      )}

      </div></div>
      <ConfirmDialog
        open={!!pendingDelete}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) void manage(pendingDelete, 'remove');
          setPendingDelete(null);
        }}
        title={t('Remove container?')}
        description={pendingDelete ? t('Container "{name}" will be force-removed (docker rm -f). Volumes are kept.', { name: pendingDelete.name }) : ''}
        confirmLabel={t('Remove')}
        confirmText={pendingDelete?.name}
        danger
        icon="trash"
      />

      <InstallDialog open={openInstall} onClose={() => setOpenInstall(false)} versions={det.data?.supported_versions ?? {}} />
      <ConnectDialog initial={connection} open={openConnect} onClose={() => setOpenConnect(false)} onConnect={instance=>{void pushRecent(instance.id);openExplorer(instance);}} />
    </div>
  );
}

const MODE_LABEL: Record<string, () => string> = {
  docker: () => 'Docker',
  native: () => t('Native'),
  local: () => t('Local port'),
  file: () => 'SQLite',
};

const DEFAULT_PORT: Record<string, string> = { mysql: '3306', mariadb: '3306', postgres: '5432', mongodb: '27017', redis: '6379', valkey: '6379' };

const DOCKER_ENGINES = [
  { value: 'mysql', label: 'MySQL' },
  { value: 'mariadb', label: 'MariaDB' },
  { value: 'postgres', label: 'PostgreSQL' },
  { value: 'mongodb', label: 'MongoDB' },
  { value: 'redis', label: 'Redis' },
  { value: 'valkey', label: 'Valkey' },
];

function randomPassword(): string {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
  const b = new Uint8Array(20);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => chars[x % chars.length]).join('');
}

function InstallDialog({ open, onClose, versions }: { open: boolean; onClose(): void; versions: Record<string, string[]> }) {
  const isWindows = getSdk().platform === 'windows';
  const [mode, setMode] = useState<'docker' | 'apt' | 'sqlite'>('docker');
  const [engine, setEngine] = useState('mysql');
  const [version, setVersion] = useState('');
  const [name, setName] = useState('db-mysql');
  const [password, setPassword] = useState(() => randomPassword());
  const [port, setPort] = useState('3306');
  const [aptEngine, setAptEngine] = useState('mariadb');
  const [sqlitePath, setSqlitePath] = useState(isWindows ? 'C:\\ProgramData\\Ervisio\\data\\database.sqlite' : '/home/ubuntu/database.sqlite');
  const [busy, setBusy] = useState(false);

  const vlist = versions[engine] ?? ['latest'];
  const ver = version && vlist.includes(version) ? version : vlist[0];

  const submit = async () => {
    setBusy(true);
    try {
      if (mode === 'docker') {
        await dbApi.installDocker({ engine, version: ver, name, password, port: parseInt(port, 10) || parseInt(DEFAULT_PORT[engine], 10) });
        toast.ok(t('Engine deployed'), t('{name} is running in Docker', { name }));
      } else if (mode === 'apt') {
        await dbApi.installApt(isWindows && aptEngine === 'postgres' ? { engine: aptEngine, password } : { engine: aptEngine });
        toast.ok(t('Native service installed'), aptEngine);
      } else {
        const res = await dbApi.createSqlite({ path: sqlitePath });
        toast.ok(t('SQLite file created'), res.path);
      }
      onClose();
      await detectCache.get([], true);
    } catch (err: any) {
      toast.err(t('Installation failed'), err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={t('New database engine')} description={t('Deploy MySQL, MariaDB, PostgreSQL, MongoDB, Redis or Valkey, or create a SQLite file')} icon="plus" size="lg"
      onSubmit={(e) => { e.preventDefault(); void submit(); }}
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('Cancel')}</Button>
        <Button variant="primary" type="submit" icon={mode === 'sqlite' ? 'plus' : 'download'} loading={busy}>
          {mode === 'docker' ? t('Deploy in Docker') : mode === 'apt' ? t('Install service') : t('Create file')}
        </Button>
      </>}
    >
      <div className="db-form">
        <Segmented
          aria-label={t('Deploy mode')}
          value={mode}
          onChange={(v) => setMode(v as typeof mode)}
          options={[{ value: 'docker', label: 'Docker' }, { value: 'apt', label: isWindows ? t('Native (winget)') : t('Native (APT)') }, { value: 'sqlite', label: 'SQLite' }]}
        />
        {mode === 'docker' && (
          <>
            <div className="db-row2">
              <Select
                label={t('Engine')}
                value={engine}
                onChange={(v) => {
                  setEngine(v);
                  setName(`db-${v}`);
                  setPort(DEFAULT_PORT[v] ?? '3306');
                  setVersion('');
                }}
                options={DOCKER_ENGINES}
              />
              <Select label={t('Version')} value={ver} onChange={setVersion} options={vlist.map((v) => ({ value: v, label: v }))} />
            </div>
            <div className="db-row2">
              <Input label={t('Container name')} value={name} onChange={(e) => setName(e.target.value)} mono />
              <Input label={t('Host port')} value={port} onChange={(e) => setPort(e.target.value)} mono />
            </div>
            <Input
              label={engine === 'redis' || engine === 'valkey' ? t('Password (requirepass)') : engine === 'mongodb' ? t('Password of the root user') : t('Root / superuser password')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              mono
              hint={t('Generated for you. Copy it now: it is needed to connect from other tools.')}
              end={<IconButton icon="refresh" label={t('Generate')} size="sm" onClick={() => setPassword(randomPassword())} />}
            />
          </>
        )}
        {mode === 'apt' && (
          <>
            <Select
              label={t('Package')}
              value={aptEngine}
              onChange={setAptEngine}
              options={isWindows ? [
                { value: 'mariadb', label: 'MariaDB Server (MariaDB.Server)' },
                { value: 'mysql', label: 'MySQL Server (Oracle.MySQL)' },
                { value: 'postgres', label: 'PostgreSQL (PostgreSQL.PostgreSQL.17)' },
                { value: 'mongodb', label: 'MongoDB Server (MongoDB.Server)' },
                { value: 'redis', label: 'Redis for Windows (Memurai.MemuraiDeveloper)' },
                { value: 'sqlite', label: 'SQLite (SQLite.SQLite)' },
              ] : [
                { value: 'mariadb', label: 'MariaDB Server (mariadb-server)' },
                { value: 'mysql', label: 'MySQL Server (mysql-server)' },
                { value: 'postgres', label: 'PostgreSQL (postgresql)' },
                { value: 'mongodb', label: 'MongoDB (mongodb-org, official repository)' },
                { value: 'redis', label: 'Redis (redis-server)' },
                { value: 'valkey', label: 'Valkey (valkey-server)' },
                { value: 'sqlite', label: 'SQLite3 tools (sqlite3)' },
              ]}
            />
            {isWindows && aptEngine === 'postgres' && (
              <Input
                label={t('Superuser password')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                mono
                hint={t('Set as the postgres password by the installer. Copy it now.')}
                end={<IconButton icon="refresh" label={t('Generate')} size="sm" onClick={() => setPassword(randomPassword())} />}
              />
            )}
            <div className="db-note"><Icon name="info" /><span>{isWindows ? t('Runs winget install in the background; the engine is registered as a Windows service.') : aptEngine === 'mongodb' ? t('Adds the official MongoDB APT repository (repo.mongodb.org), installs mongodb-org, then enables and starts mongod.') : t('Runs apt-get install in the background, then enables and starts the systemd service.')}</span></div>
          </>
        )}
        {mode === 'sqlite' && (
          <>
            <PathInput mode="save" label={t('File path')} value={sqlitePath} onPathChange={setSqlitePath} mono />
            <div className="db-note"><Icon name="info" /><span>{t('Creates an empty SQLite database file on the server.')}</span></div>
          </>
        )}
      </div>
    </Dialog>
  );
}

type ConnectType = 'mysql' | 'postgres' | 'mongodb' | 'redis';
const CONNECT_DEFAULTS: Record<ConnectType, { port: string; user: string }> = {
  mysql: { port: '3306', user: 'root' },
  postgres: { port: '5432', user: 'postgres' },
  mongodb: { port: '27017', user: '' },
  redis: { port: '6379', user: '' },
};

function ConnectDialog({ initial, open, onClose, onConnect }: { initial:DbInstance|null; open: boolean; onClose(): void; onConnect(inst: DbInstance): void }) {
  const [type, setType] = useState<ConnectType>('mysql');
  const [host, setHost] = useState('127.0.0.1');
  const [port, setPort] = useState('3306');
  const [user, setUser] = useState('root');
  const [pass, setPass] = useState('');
  const [name, setName] = useState('');

  const [tls,setTls]=useState(false);const [caFile,setCaFile]=useState('');const [authDb,setAuthDb]=useState('admin');
  const [rememberPassword,setRememberPassword]=useState(false);const [saving,setSaving]=useState(false);const [error,setError]=useState('');
  useEffect(()=>{if(!open)return;setError('');setRememberPassword(!!initial?.password);setTls(!!initial?.tls);setCaFile(initial?.ca_file||'');setAuthDb(initial?.auth_db||'admin');setType((initial?.engine as ConnectType)||'mysql');setHost(initial?.host||'127.0.0.1');setPort(String(initial?.port||3306));setUser(initial?.user||'root');setPass(initial?.password||'');setName(initial?.name||'');},[open,initial]);
  const submit = async () => {
    const inst: DbInstance = {
      ...(initial||{}),
      id: initial?.id || `custom-${crypto.randomUUID()}`,
      name: name.trim() || (user ? `${user}@${host}:${port}` : `${host}:${port}`),
      type,
      engine: type,
      mode: initial?.mode||'local',
      host,
      port: parseInt(port, 10),
      user,
      password: pass, tls, ca_file:tls?caFile:undefined, auth_db:type==='mongodb'?authDb:undefined,
      status: initial?.status||'ready',
    };
    if(!host.trim()||!Number.isInteger(inst.port)||inst.port!<1||inst.port!>65535){setError(t('Enter a valid host and port.'));return;}
    setSaving(true);try{await rememberInstance(inst,rememberPassword);onClose();onConnect(inst);}catch(error){setError(error instanceof Error?error.message:String(error));}finally{setSaving(false);}
  };

  return (
    <Dialog open={open} onClose={onClose} title={t('Connect to a database')} description={t('A local or remote MySQL, MariaDB, PostgreSQL, MongoDB or Redis server')} icon="link"
      onSubmit={(e) => { e.preventDefault(); void submit(); }}
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('Cancel')}</Button>
        <Button variant="primary" type="submit" icon="link" loading={saving}>{t('Connect')}</Button>
      </>}
    >
      <div className="db-form">
        <div className="db-row2">
          <Input label={t('Display name')} value={name} onChange={(e) => setName(e.target.value)} placeholder={t('Optional')} />
          <Select
            label={t('Engine')}
            value={type}
            onChange={(v) => {
              const d = CONNECT_DEFAULTS[v as ConnectType];
              setType(v as ConnectType);
              setPort(d.port);
              setUser(d.user);
            }}
            options={[
              { value: 'mysql', label: 'MySQL / MariaDB' },
              { value: 'postgres', label: 'PostgreSQL' },
              { value: 'mongodb', label: 'MongoDB' },
              { value: 'redis', label: 'Redis / Valkey / KeyDB' },
            ]}
          />
        </div>
        <div className="db-row2">
          <Input label={t('Host')} value={host} onChange={(e) => setHost(e.target.value)} mono />
          <Input label={t('Port')} value={port} onChange={(e) => setPort(e.target.value)} mono />
        </div>
        <div className="db-row2">
          <Input label={t('Username')} value={user} onChange={(e) => setUser(e.target.value)} mono placeholder={type === 'mongodb' || type === 'redis' ? t('Optional') : undefined} />
          <Input type="password" label={t('Password')} value={pass} onChange={(e) => setPass(e.target.value)} />
        </div>
        <label><input type="checkbox" checked={tls} onChange={event=>setTls(event.target.checked)}/>{t('Use TLS and verify the server certificate')}</label>
        {tls&&<PathInput label={t('CA certificate (optional)')} value={caFile} onPathChange={setCaFile} mono/>}
        {type==='mongodb'&&<Input label={t('Authentication database')} value={authDb} onChange={event=>setAuthDb(event.target.value)} mono/>}
        {error&&<p role="alert" className="db-library-error">{error}</p>}<label><input type="checkbox" checked={rememberPassword} onChange={event=>setRememberPassword(event.target.checked)}/>{t('Remember credentials in my private account library')}</label>
        <div className="db-note"><Icon name="key" /><span>{t('Connections are saved for your account. Passwords stay in memory unless you explicitly choose to remember them.')}</span></div>
      </div>
    </Dialog>
  );
}
