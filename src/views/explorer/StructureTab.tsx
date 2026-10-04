import { structureCache, type DbInstance } from '../../api';
import { t } from '../../i18n';
import { Badge, Button, IconButton, toast } from '../../kit';
import { copyText } from '../../utils';

export function StructureTab({ inst, db, table }: { inst: DbInstance; db: string; table: string }) {
  const s = structureCache.use([inst, db, table]);
  const data = s.data;

  const copyDdl = async () => {
    const ok = await copyText(data?.create_sql || '');
    if (ok) toast.ok(t('DDL copied'));
    else toast.warn(t('Copy blocked'), t('Select the DDL manually'));
  };

  if (!data) {
    if (s.error) return <div className="db-qerr">{s.error}</div>;
    return <div className="db-empty-pane">{t('Loading…')}</div>;
  }

  return (
    <div className="db-col">
      <div className="db-card-h">
        <h3>{t('Columns')} <span className="db-muted">{data.columns.length}</span></h3>
        <IconButton icon="refresh" label={t('Reload')} loading={s.loading} onClick={() => void s.refresh()} />
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="db-dg" style={{ tableLayout: 'auto', width: '100%' }}>
          <thead>
            <tr><th>#</th><th>{t('Column')}</th><th>{t('Type')}</th><th>{t('Null')}</th><th>{t('Key')}</th><th>{t('Default')}</th><th>{t('Extra')}</th><th>{t('Comment')}</th></tr>
          </thead>
          <tbody>
            {data.columns.map((c, i) => (
              <tr key={c.name}>
                <td className="db-muted" style={{ maxWidth: 'none' }}>{i + 1}</td>
                <td style={{ fontWeight: 700, maxWidth: 'none' }}>{c.name}</td>
                <td style={{ color: 'var(--info)', maxWidth: 'none' }}>{c.type}</td>
                <td style={{ maxWidth: 'none' }}>{c.nullable === 'YES' ? t('yes') : t('no')}</td>
                <td style={{ maxWidth: 'none' }}>{c.key_type ? <Badge tone={c.key_type === 'PRI' ? 'warn' : 'info'}>{c.key_type}</Badge> : ''}</td>
                <td style={{ maxWidth: 'none' }}>{c.default_val === null || c.default_val === undefined ? <span className="db-null">NULL</span> : String(c.default_val)}</td>
                <td style={{ maxWidth: 'none' }}>{c.extra}</td>
                <td style={{ maxWidth: 'none', fontFamily: 'inherit' }}>{c.comment}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.indexes.length > 0 && (
        <>
          <div className="db-card-h"><h3>{t('Indexes')} <span className="db-muted">{data.indexes.length}</span></h3></div>
          <div style={{ overflowX: 'auto' }}>
            <table className="db-dg" style={{ tableLayout: 'auto', width: '100%' }}>
              <thead><tr><th>{t('Index')}</th><th>{t('Column')}</th><th>{t('Kind')}</th></tr></thead>
              <tbody>
                {data.indexes.map((idx, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 700, maxWidth: 'none' }}>{idx.name}</td>
                    <td style={{ maxWidth: 'none', whiteSpace: 'normal' }}>{idx.column}</td>
                    <td style={{ maxWidth: 'none' }}>{idx.name === 'PRIMARY' ? <Badge tone="warn">PRIMARY</Badge> : idx.unique ? <Badge tone="ok">UNIQUE</Badge> : 'INDEX'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {data.create_sql && (
        <>
          <div className="db-card-h">
            <h3>{t('Definition (DDL)')}</h3>
            <Button variant="secondary" size="sm" icon="copy" onClick={copyDdl}>{t('Copy DDL')}</Button>
          </div>
          <pre className="db-code">{data.create_sql}</pre>
        </>
      )}
    </div>
  );
}
