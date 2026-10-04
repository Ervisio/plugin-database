import { useEffect, useState } from 'react';
import { getSdk } from '../sdk';
import { t } from '../i18n';
import { Badge, EmptyState, IconButton, Skeleton } from '../kit';
import { PageHeader } from '../ui/PageHeader';

interface AuditEntry {
  time: string;
  user: string;
  action: string;
  target?: string;
  result: string;
  detail?: string;
}

let cached: AuditEntry[] | null = null;

export function ActivityView() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(cached);
  const [loading, setLoading] = useState(!cached);
  const [enabled, setEnabled] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const sdk = getSdk();
      if (!sdk.audit?.list) {
        setEnabled(false);
        return;
      }
      const res = await sdk.audit.list({ limit: 100 });
      cached = (res.entries || []) as AuditEntry[];
      setEntries(cached);
      setEnabled(res.enabled !== false);
    } catch {
      setEnabled(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!cached) void load();
  }, []);

  const header = (
    <PageHeader
      icon="clock"
      title={t('Activity')}
      subtitle={t('Commands run by this plugin')}
      actions={<IconButton icon="refresh" label={t('Refresh')} loading={loading} onClick={() => void load()} />}
    />
  );

  if (!entries && loading) return <div className="db-col">{header}<Skeleton lines={6} /></div>;
  if (!enabled) {
    return <div className="db-col">{header}<EmptyState icon="clock" title={t('Activity log unavailable')} text={t('The activity log is off, or this console is older than Ervisio 0.5.')} /></div>;
  }
  if (!entries || entries.length === 0) {
    return <div className="db-col">{header}<EmptyState icon="clock" title={t('No activity yet')} text={t('Every command this plugin runs shows up here.')} /></div>;
  }
  return (
    <div className="db-col">
      {header}
      <div className="db-act">
        {entries.map((e, i) => (
          <div key={i} className="db-act-it">
            <Badge tone={e.result === 'ok' ? 'ok' : e.result === 'denied' ? 'warn' : 'err'}>{e.result}</Badge>
            <div style={{ minWidth: 0 }}>
              <b>{e.target || e.action}</b>
              {e.detail && <div className="db-muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.detail}</div>}
            </div>
            <div className="db-act-m">
              <div>{e.user}</div>
              <div>{new Date(e.time).toLocaleString()}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
