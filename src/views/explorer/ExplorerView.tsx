import { databasesCache, isNoSql, tablesCache, type DbInstance } from '../../api';
import { patchExplorer, useExplorer, type ExplorerTab } from '../../explorerState';
import { t } from '../../i18n';
import { Button, EmptyState, Tabs, type TabItem } from '../../kit';
import { PageHeader } from '../../ui/PageHeader';
import { DataGrid } from './DataGrid';
import { StructureTab } from './StructureTab';
import { QueryTab } from './QueryTab';
import { UsersTab } from './UsersTab';
import { LogsTab } from './LogsTab';
import { formatCount } from './SchemaTree';

/** Main pane of an engine. The database/table tree lives in the sidebar (ExplorerNav). */
export function ExplorerView({ inst }: { inst: DbInstance }) {
  const st = useExplorer(inst.id);
  const dbs = databasesCache.use([inst]);
  const tables = tablesCache.use(st.db ? [inst, st.db] : null);
  const info = tables.data?.find((x) => x.name === st.table);

  const items: TabItem[] = [
    { id: 'data', label: t('Data'), icon: 'grid' },
    { id: 'structure', label: t('Structure'), icon: 'columns' },
    { id: 'query', label: t('Query'), icon: 'code' },
  ];
  if (inst.engine !== 'sqlite') items.push({ id: 'users', label: t('Users'), icon: 'users' });
  if (inst.engine === 'mysql' || isNoSql(inst.engine)) items.push({ id: 'logs', label: inst.engine === 'mongodb' ? t('Profiler') : t('Slow log'), icon: 'logs' });
  const tab: ExplorerTab = items.some((i) => i.id === st.tab) ? st.tab : 'data';
  const needsTable = tab === 'data' || tab === 'structure';
  const count = tables.data?.length ?? '…';
  const tableCount = inst.engine === 'mongodb' ? t('{n} collections', { n: count }) : inst.engine === 'redis' ? t('{n} key patterns', { n: count }) : t('{n} tables', { n: count });
  const rowsLabel = (n: number) => (inst.engine === 'mongodb' ? t('~{n} documents', { n: formatCount(n) }) : inst.engine === 'redis' ? t('~{n} keys', { n: formatCount(n) }) : t('~{n} rows', { n: formatCount(n) }));

  if (dbs.error && !dbs.data) {
    return (
      <EmptyState
        icon="alert"
        title={t('Cannot reach {name}', { name: inst.name })}
        text={dbs.error}
        action={<Button variant="primary" icon="refresh" onClick={() => void dbs.refresh()}>{t('Retry')}</Button>}
      />
    );
  }

  const title = st.table && needsTable ? st.table : st.db || inst.name;
  const subtitle = st.table && needsTable
    ? [st.db, info ? (/view/i.test(info.type) ? t('view') : rowsLabel(info.rows_count ?? 0)) : '', info?.engine].filter(Boolean).join(' · ')
    : st.db
      ? tableCount
      : t('Pick a database in the sidebar');

  return (
    <div className="db-ex">
      <div className="db-ex-h">
        <PageHeader icon={st.table && needsTable ? 'db-table' : 'db-database'} title={title} subtitle={subtitle} />
        <Tabs items={items} value={tab} onChange={(id) => patchExplorer(inst.id, { tab: id as ExplorerTab })} aria-label={t('Table views')} />
      </div>
      <div className="db-pane">
        {needsTable && !st.table ? (
          <div className="db-empty-pane">
            {st.db ? (inst.engine === 'mongodb' ? t('Pick a collection in the sidebar, or open the Query tab.') : inst.engine === 'redis' ? t('Pick a key pattern in the sidebar, or open the Query tab.') : t('Pick a table in the sidebar, or open the Query tab.')) : t('Pick a database in the sidebar.')}
          </div>
        ) : tab === 'data' ? (
          <DataGrid key={`${st.db}.${st.table}`} inst={inst} db={st.db} table={st.table} />
        ) : tab === 'structure' ? (
          <StructureTab inst={inst} db={st.db} table={st.table} />
        ) : tab === 'query' ? (
          <QueryTab inst={inst} db={st.db} table={st.table} onDbChange={(db) => patchExplorer(inst.id, (s) => ({ db, table: db === s.db ? s.table : '' }))} />
        ) : tab === 'users' ? (
          <UsersTab inst={inst} db={st.db} />
        ) : (
          <LogsTab inst={inst} db={st.db} />
        )}
      </div>
    </div>
  );
}
