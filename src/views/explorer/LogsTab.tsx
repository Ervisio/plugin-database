import { useEffect, useState } from 'react';
import { dbApi, logsCache, type DbInstance } from '../../api';
import { t } from '../../i18n';
import { Button, IconButton, Input, Switch, toast } from '../../kit';

export function LogsTab({ inst }: { inst: DbInstance }) {
  const logs = logsCache.use([inst]);
  const [enabled, setEnabled] = useState(false);
  const [threshold, setThreshold] = useState('2.0');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!logs.data) return;
    setEnabled(logs.data.status?.slow_query_log === 'ON');
    setThreshold(String(logs.data.status?.long_query_time || '2.0'));
  }, [logs.data]);

  const save = async () => {
    setBusy(true);
    try {
      await dbApi.toggleLog(inst, { enable: enabled, threshold: parseFloat(threshold) || 2.0 });
      toast.ok(t('Slow query settings saved'));
      void logs.refresh();
    } catch (err: any) {
      toast.err(t('Save failed'), err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="db-col">
      <div className="db-card">
        <div className="db-card-h">
          <h3>{t('Slow query log')}</h3>
          <Switch checked={enabled} onChange={setEnabled} aria-label={t('Enable slow query log')} />
        </div>
        <div className="db-muted">{t('Logs every query that runs longer than the threshold.')}</div>
        <div className="db-row" style={{ alignItems: 'flex-end' }}>
          <div style={{ width: 200 }}>
            <Input label={t('Threshold (seconds)')} value={threshold} onChange={(e) => setThreshold(e.target.value)} mono />
          </div>
          <Button variant="primary" size="sm" loading={busy} onClick={save}>{t('Save')}</Button>
        </div>
      </div>
      <div className="db-card-h">
        <h3>{t('Log content')}</h3>
        <IconButton icon="refresh" label={t('Reload')} loading={logs.loading} onClick={() => void logs.refresh()} />
      </div>
      {logs.error && <div className="db-qerr">{logs.error}</div>}
      <pre className="db-code">{logs.data ? logs.data.content || t('No slow queries logged yet.') : t('Loading…')}</pre>
    </div>
  );
}
