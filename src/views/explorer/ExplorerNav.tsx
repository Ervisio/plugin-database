import { navigate } from '../../router';
import { t } from '../../i18n';
import { Icon, IconButton, toast } from '../../kit';
import { engineIcon } from '../../ui/icons';
import { buildConnectionString, copyText } from '../../utils';
import type { DbInstance } from '../../api';
import { SchemaTree } from './SchemaTree';

/** Sidebar inside an engine: back to Engines, the current engine, then the database/table tree. */
export function ExplorerNav({ inst, filter }: { inst: DbInstance; filter: string }) {
  const where = inst.mode === 'file' ? inst.path : `${inst.host || '127.0.0.1'}${inst.port ? `:${inst.port}` : ''}`;
  const up = inst.status !== 'stopped';

  const copyConn = async () => {
    const ok = await copyText(buildConnectionString(inst));
    if (ok) toast.ok(t('Connection string copied'));
    else toast.warn(t('Copy blocked'), t('The frame denied clipboard access'));
  };

  return (
    <nav className="db-nav db-nav--tree" aria-label={t('Database navigation')}>
      <div className="db-nav-env">
        <button type="button" className="db-nav-back" onClick={() => navigate({ view: 'engines' }, { root: true })}>
          <Icon name="chevronleft" />
          {t('Engines')}
        </button>
        <div className="db-nav-cur hue-plg">
          <span className="db-nav-cur-ic"><Icon name={engineIcon(inst.type)} /></span>
          <div className="db-nav-cur-t">
            <b title={inst.name}>{inst.name}</b>
            <span title={where}>
              <span className={`db-dot ${up ? 'db-dot--ok' : 'db-dot--warn'}`} />
              {inst.type.toUpperCase()} · {where}
            </span>
          </div>
          <IconButton icon="copy" label={t('Copy connection string')} size="sm" onClick={copyConn} />
        </div>
      </div>
      <SchemaTree inst={inst} filter={filter} />
    </nav>
  );
}
