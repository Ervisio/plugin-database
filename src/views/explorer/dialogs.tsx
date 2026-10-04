import { useEffect, useState } from 'react';
import { dbApi, invalidateInstance, type ColumnMeta, type DbInstance } from '../../api';
import { patchExplorer } from '../../explorerState';
import { t } from '../../i18n';
import { Button, Checkbox, Dialog, Input, toast } from '../../kit';

export function CreateDbDialog({ inst, open, onClose }: { inst: DbInstance; open: boolean; onClose(): void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) setName('');
  }, [open]);

  const submit = async () => {
    const n = name.trim();
    if (!n) return;
    setBusy(true);
    try {
      await dbApi.createDatabase(inst, { name: n });
      toast.ok(t('Database created'), n);
      invalidateInstance(inst, 'dbs');
      patchExplorer(inst.id, (s) => ({ db: n, table: '', expanded: [...s.expanded.filter((x) => x !== n), n] }));
      onClose();
    } catch (err: any) {
      toast.err(t('Create database failed'), err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={t('New database')} icon="db-database" width="sm"
      onSubmit={(e) => { e.preventDefault(); void submit(); }}
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('Cancel')}</Button>
        <Button variant="primary" type="submit" loading={busy} disabled={!name.trim()}>{t('Create')}</Button>
      </>}
    >
      <div className="db-form">
        <Input label={t('Name')} placeholder="my_project" value={name} autoFocus onChange={(e) => setName(e.target.value)} mono />
      </div>
    </Dialog>
  );
}

/**
 * Insert a row (or duplicate one). Auto-increment columns are left out unless filled; each column can be NULL.
 * Empty fields are not sent, so the column default applies.
 */
export function RowDialog({ inst, db, table, meta, initial, open, onClose, onSaved }: {
  inst: DbInstance;
  db: string;
  table: string;
  meta: ColumnMeta[];
  initial?: Record<string, any> | null;
  open: boolean;
  onClose(): void;
  onSaved(): void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [nulls, setNulls] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const v: Record<string, string> = {};
    const n: Record<string, boolean> = {};
    for (const c of meta) {
      const src = initial?.[c.name];
      if (initial && !c.auto) {
        if (src === null) n[c.name] = true;
        else v[c.name] = String(src ?? '');
      } else v[c.name] = '';
    }
    setValues(v);
    setNulls(n);
  }, [open, initial, meta]);

  const submit = async () => {
    const data: Record<string, any> = {};
    for (const c of meta) {
      if (nulls[c.name]) data[c.name] = null;
      else if (values[c.name] !== '') data[c.name] = values[c.name];
    }
    setBusy(true);
    try {
      await dbApi.saveRow(inst, { database: db, table, data, is_new: true });
      toast.ok(t('Row inserted'), table);
      invalidateInstance(inst, 'tables', db);
      onSaved();
      onClose();
    } catch (err: any) {
      toast.err(t('Insert failed'), err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={initial ? t('Duplicate row') : t('Insert row')} description={`${db}.${table}`} icon="plus" size="lg"
      onSubmit={(e) => { e.preventDefault(); void submit(); }}
      footer={<>
        <Button variant="ghost" onClick={onClose}>{t('Cancel')}</Button>
        <Button variant="primary" type="submit" loading={busy}>{t('Insert')}</Button>
      </>}
    >
      <div className="db-form" style={{ maxHeight: '60vh', overflowY: 'auto', paddingRight: 4 }}>
        {meta.map((c) => {
          const hint = [c.type, c.pk ? 'PK' : '', c.auto ? 'auto' : '', c.default !== null && c.default !== undefined ? `${t('default')} ${c.default}` : ''].filter(Boolean).join(' · ');
          return (
            <div key={c.name} className="db-row" style={{ alignItems: 'flex-end', flexWrap: 'nowrap' }}>
              <div className="db-grow">
                <Input
                  label={c.name}
                  hint={hint}
                  mono
                  disabled={!!nulls[c.name]}
                  value={nulls[c.name] ? '' : values[c.name] ?? ''}
                  placeholder={nulls[c.name] ? 'NULL' : c.auto ? t('auto') : ''}
                  onChange={(e) => setValues({ ...values, [c.name]: e.target.value })}
                />
              </div>
              {c.nullable && (
                <div style={{ paddingBottom: 26 }}>
                  <Checkbox label="NULL" checked={!!nulls[c.name]} onChange={(v) => setNulls({ ...nulls, [c.name]: v })} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Dialog>
  );
}

