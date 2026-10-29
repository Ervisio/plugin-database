import { useState } from 'react';
import { dbApi, invalidateInstance, usersCache, type DbInstance, type DbUser } from '../../api';
import { t } from '../../i18n';
import { Badge, Button, Checkbox, ConfirmDialog, Dialog, IconButton, Input, toast } from '../../kit';

const GRANTS = ['ALL PRIVILEGES', 'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'INDEX', 'ALTER'];

export function UsersTab({ inst, db }: { inst: DbInstance; db: string }) {
  const users = usersCache.use([inst]);
  const sqlHost = inst.engine === 'mysql';
  const superUser = (u: string) => u === 'root' || u === 'postgres' || (inst.engine === 'redis' && u === 'default');
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [host, setHost] = useState('%');
  const [pass, setPass] = useState('');
  const [grants, setGrants] = useState<string[]>(['ALL PRIVILEGES']);
  const [busy, setBusy] = useState(false);
  const [pendingDrop, setPendingDrop] = useState<DbUser | null>(null);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await dbApi.createUser(inst, { user: name.trim(), host, password: pass, grants, database: db || '*' });
      toast.ok(t('User created'), `${name}@${host}`);
      setOpen(false);
      setName('');
      setPass('');
      invalidateInstance(inst, 'users');
    } catch (err: any) {
      toast.err(t('Create user failed'), err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="db-col">
      <div className="db-card-h">
        <h3>{t('Users & grants')} {users.data && <span className="db-muted">{users.data.length}</span>}</h3>
        <IconButton icon="refresh" label={t('Reload')} loading={users.loading} onClick={() => void users.refresh()} />
        <Button variant="primary" icon="plus" size="sm" onClick={() => setOpen(true)}>{t('New user')}</Button>
      </div>
      {users.error && <div className="db-qerr">{users.error}</div>}
      {!users.data && users.loading && <div className="db-empty-pane">{t('Loading…')}</div>}
      {users.data && (
        <div style={{ overflowX: 'auto' }}>
          <table className="db-dg" style={{ tableLayout: 'auto', width: '100%' }}>
            <thead><tr><th>{t('User')}</th><th>{inst.engine === 'mongodb' ? t('Auth database') : t('Host')}</th><th>{inst.engine === 'redis' ? t('ACL rules') : inst.engine === 'mongodb' ? t('Roles') : t('Privileges')}</th><th /></tr></thead>
            <tbody>
              {users.data.map((u, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 700, maxWidth: 'none' }}>{u.user}</td>
                  <td style={{ maxWidth: 'none' }}>{u.host}</td>
                  <td style={{ maxWidth: 'none', whiteSpace: 'normal' }}><Badge tone={superUser(u.user) ? 'ok' : 'info'}>{u.privileges || '—'}</Badge></td>
                  <td style={{ textAlign: 'right', maxWidth: 'none' }}>
                    {!superUser(u.user) && (
                      <IconButton icon="trash" label={t('Drop user')} size="sm" onClick={() => setPendingDrop(u)} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} title={t('New database user')} description={db ? t('Grants apply to {db}', { db }) : t('Grants apply to all databases')} icon="user"
        onSubmit={(e) => { e.preventDefault(); void create(); }}
        footer={<>
          <Button variant="ghost" onClick={() => setOpen(false)}>{t('Cancel')}</Button>
          <Button variant="primary" type="submit" loading={busy} disabled={!name.trim()}>{t('Create user')}</Button>
        </>}
      >
        <div className="db-form">
          <div className="db-row2">
            <Input label={t('Username')} value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="appuser" />
            {sqlHost && <Input label={t('Host')} value={host} onChange={(e) => setHost(e.target.value)} placeholder="%" hint={t('% = any host')} />}
          </div>
          {inst.engine === 'redis' && <div className="db-muted">{t('Redis ACL: SELECT = +@read, other grants = +@write, ALL PRIVILEGES = +@all, on every key and database.')}</div>}
          {inst.engine === 'mongodb' && <div className="db-muted">{t('MongoDB roles: SELECT = read, other grants = readWrite, ALL PRIVILEGES = dbOwner (root on all databases).')}</div>}
          <Input type="password" label={t('Password')} value={pass} onChange={(e) => setPass(e.target.value)} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {GRANTS.map((g) => (
              <Checkbox key={g} label={g} checked={grants.includes(g)} onChange={(c) => setGrants(c ? [...grants, g] : grants.filter((x) => x !== g))} />
            ))}
          </div>
        </div>
      </Dialog>

      <ConfirmDialog
        open={!!pendingDrop}
        onClose={() => setPendingDrop(null)}
        onConfirm={async () => {
          const u = pendingDrop;
          setPendingDrop(null);
          if (!u) return;
          try {
            await dbApi.dropUser(inst, u.user, u.host);
            toast.ok(t('User dropped'), u.user);
            invalidateInstance(inst, 'users');
          } catch (err: any) {
            toast.err(t('Drop user failed'), err.message);
          }
        }}
        title={t('Drop user?')}
        description={pendingDrop ? t("'{user}'@'{host}' will be removed.", { user: pendingDrop.user, host: pendingDrop.host }) : ''}
        confirmLabel={t('Drop user')}
        danger
        icon="trash"
      />
    </div>
  );
}
