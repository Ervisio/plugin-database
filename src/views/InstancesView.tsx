import { useState, type MouseEvent } from 'react';
import { Button, ConfirmDialog, Dialog, EmptyState, Icon, IconButton, Input, Select, Skeleton, StatCard, Segmented, toast } from '../kit';
import { dbApi, detectCache, invalidateInstance, type DbInstance } from '../api';
import { t } from '../i18n';
import { openExplorer } from '../router';
import { PageHeader } from '../ui/PageHeader';
import { engineIcon } from '../ui/icons';
import { buildConnectionString, copyText } from '../utils';
import { pushRecent, useFile } from '../settings';
import { getSdk } from '../sdk';

export function InstancesView({ search }: { search: string }) {
  const det = detectCache.use([]);
  const [recent] = useFile('recent');

  const [openInstall, setOpenInstall] = useState(false);
  const [openConnect, setOpenConnect] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<DbInstance | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const instances = det.data?.instances ?? [];
  const sqliteFiles = det.data?.sqlite_files ?? [];
  const all = [...instances, ...sqliteFiles];
  const q = search.trim().toLowerCase();
  const filtered = all.filter((i) => !q || [i.name, i.type, i.mode, i.host, String(i.port || ''), i.path || ''].join(' ').toLowerCase().includes(q));
  const running = all.filter((i) => i.status !== 'stopped').length;
  const recentList = recent.ids.map((id) => filtered.find((i) => i.id === id)).filter(Boolean) as DbInstance[];
  const rest = filtered.filter((i) => !recent.ids.includes(i.id));

  const open = (inst: DbInstance) => {
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

  const card = (inst: DbInstance) => {
    const up = inst.status !== 'stopped';
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
            <span>{inst.type.toUpperCase()}{inst.version ? ` ${inst.version}` : ''} · {inst.mode === 'docker' ? 'Docker' : inst.mode === 'native' ? t('Native') : 'SQLite'}</span>
          </div>
          <span className={`db-dot ${up ? 'db-dot--ok' : 'db-dot--warn'}`} title={inst.status} />
        </div>
        {inst.port ? <div className="db-kv"><span>{t('Address')}</span><code>{inst.host || '127.0.0.1'}:{inst.port}</code></div> : null}
        {inst.path && <div className="db-kv"><span>{t('File')}</span><code>{inst.path}</code></div>}
        <div className="db-eng-f" onClick={stop}>
          {inst.mode === 'docker' && (
            <>
              {up
                ? <IconButton icon="stop" label={t('Stop')} size="sm" loading={busyId === inst.id} onClick={() => manage(inst, 'stop')} />
                : <IconButton icon="play" label={t('Start')} size="sm" loading={busyId === inst.id} onClick={() => manage(inst, 'start')} />}
              <IconButton icon="refresh" label={t('Restart')} size="sm" onClick={() => manage(inst, 'restart')} />
              <IconButton icon="trash" label={t('Remove container')} size="sm" onClick={() => setPendingDelete(inst)} />
            </>
          )}
          <IconButton icon="copy" label={t('Copy connection string')} size="sm" onClick={() => copyConn(inst)} />
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
        subtitle={t('MySQL, MariaDB, PostgreSQL and SQLite on this server')}
        actions={
          <>
            <IconButton icon="refresh" label={t('Refresh')} loading={det.loading} onClick={() => void det.refresh()} />
            <IconButton icon="download" label={t('Export list (no passwords)')} onClick={exportList} />
            <Button variant="secondary" icon="link" onClick={() => setOpenConnect(true)}>{t('Connect')}</Button>
            <Button variant="primary" icon="plus" onClick={() => setOpenInstall(true)}>{t('New engine')}</Button>
          </>
        }
      />

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
              title={q ? t('No engines match “{q}”', { q: search }) : t('No databases found')}
              text={q ? undefined : t('No MySQL, MariaDB, PostgreSQL or SQLite database was detected. Deploy one with one click.')}
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
      <ConnectDialog open={openConnect} onClose={() => setOpenConnect(false)} onConnect={open} />
    </div>
  );
}

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
        await dbApi.installDocker({ engine, version: ver, name, password, port: parseInt(port, 10) || 3306 });
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
    <Dialog open={open} onClose={onClose} title={t('New database engine')} description={t('Deploy MySQL, MariaDB or PostgreSQL, or create a SQLite file')} icon="plus" size="lg"
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
                  setPort(v === 'postgres' ? '5432' : '3306');
                  setVersion('');
                }}
                options={[{ value: 'mysql', label: 'MySQL' }, { value: 'mariadb', label: 'MariaDB' }, { value: 'postgres', label: 'PostgreSQL' }]}
              />
              <Select label={t('Version')} value={ver} onChange={setVersion} options={vlist.map((v) => ({ value: v, label: v }))} />
            </div>
            <div className="db-row2">
              <Input label={t('Container name')} value={name} onChange={(e) => setName(e.target.value)} mono />
              <Input label={t('Host port')} value={port} onChange={(e) => setPort(e.target.value)} mono />
            </div>
            <Input
              label={t('Root / superuser password')}
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
                { value: 'sqlite', label: 'SQLite (SQLite.SQLite)' },
              ] : [
                { value: 'mariadb', label: 'MariaDB Server (mariadb-server)' },
                { value: 'mysql', label: 'MySQL Server (mysql-server)' },
                { value: 'postgres', label: 'PostgreSQL (postgresql)' },
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
            <div className="db-note"><Icon name="info" /><span>{isWindows ? t('Runs winget install in the background; the engine is registered as a Windows service.') : t('Runs apt-get install in the background, then enables and starts the systemd service.')}</span></div>
          </>
        )}
        {mode === 'sqlite' && (
          <>
            <Input label={t('File path')} value={sqlitePath} onChange={(e) => setSqlitePath(e.target.value)} mono />
            <div className="db-note"><Icon name="info" /><span>{t('Creates an empty SQLite database file on the server.')}</span></div>
          </>
        )}
      </div>
    </Dialog>
  );
}

function ConnectDialog({ open, onClose, onConnect }: { open: boolean; onClose(): void; onConnect(inst: DbInstance): void }) {
  const [type, setType] = useState<'mysql' | 'postgres'>('mysql');
  const [host, setHost] = useState('127.0.0.1');
  const [port, setPort] = useState('3306');
  const [user, setUser] = useState('root');
  const [pass, setPass] = useState('');
  const [name, setName] = useState('');

  const submit = () => {
    const inst: DbInstance = {
      id: `custom-${type}-${host}-${port}-${user}`,
      name: name.trim() || `${user}@${host}:${port}`,
      type,
      engine: type,
      mode: 'native',
      host,
      port: parseInt(port, 10),
      user,
      password: pass,
      status: 'ready',
    };
    onClose();
    onConnect(inst);
  };

  return (
    <Dialog open={open} onClose={onClose} title={t('Connect to a database')} description={t('A local or remote MySQL, MariaDB or PostgreSQL server')} icon="link"
      onSubmit={(e) => { e.preventDefault(); submit(); }}
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('Cancel')}</Button>
        <Button variant="primary" type="submit" icon="link">{t('Connect')}</Button>
      </>}
    >
      <div className="db-form">
        <div className="db-row2">
          <Input label={t('Display name')} value={name} onChange={(e) => setName(e.target.value)} placeholder={t('Optional')} />
          <Select
            label={t('Engine')}
            value={type}
            onChange={(v) => {
              setType(v as 'mysql' | 'postgres');
              setPort(v === 'postgres' ? '5432' : '3306');
              setUser(v === 'postgres' ? 'postgres' : 'root');
            }}
            options={[{ value: 'mysql', label: 'MySQL / MariaDB' }, { value: 'postgres', label: 'PostgreSQL' }]}
          />
        </div>
        <div className="db-row2">
          <Input label={t('Host')} value={host} onChange={(e) => setHost(e.target.value)} mono />
          <Input label={t('Port')} value={port} onChange={(e) => setPort(e.target.value)} mono />
        </div>
        <div className="db-row2">
          <Input label={t('Username')} value={user} onChange={(e) => setUser(e.target.value)} mono />
          <Input type="password" label={t('Password')} value={pass} onChange={(e) => setPass(e.target.value)} />
        </div>
        <div className="db-note"><Icon name="key" /><span>{t('Credentials stay in memory for this session only; they are never saved.')}</span></div>
      </div>
    </Dialog>
  );
}
