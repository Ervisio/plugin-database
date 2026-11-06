import { useEffect, useState } from 'react';
import { dbApi } from '../api';
import { t } from '../i18n';
import { Button, ConfirmDialog, toast } from '../kit';

export function RuntimeDrivers({ onReady }: { onReady?(): void }) {
  const [ready, setReady] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    void dbApi.runtimeStatus().then((v) => { if (active) setReady(v.ready); }, (e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  const prepare = async () => {
    setConfirm(false); setBusy(true); setError('');
    try {
      await dbApi.prepareRuntime();
      setReady(true); onReady?.(); toast.ok(t('Database drivers are ready'));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  if (ready === true) return null;
  if (ready === null && !error) return null;
  return <>
    <div className="db-note" role={error ? 'alert' : 'status'}>
      <div style={{ flex: 1 }}><b>{t('Database drivers')}</b><div>{error || t('Prepare PostgreSQL and MySQL drivers for this account. SQLite works without additional drivers.')}</div></div>
      <Button icon="download" loading={busy} onClick={() => setConfirm(true)}>{t('Prepare drivers')}</Button>
    </div>
    <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} onConfirm={() => void prepare()} title={t('Prepare database drivers?')} description={t('Download verified Python wheels from PyPI into a private runtime for the account executing this command. System Python and existing databases stay unchanged. This may take a few minutes.')} confirmLabel={t('Prepare drivers')} />
  </>;
}
