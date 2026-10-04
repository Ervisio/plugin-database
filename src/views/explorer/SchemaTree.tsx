import { useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { databasesCache, dbApi, invalidateInstance, isSystemDb, tablesCache, type DbInstance, type TableInfo } from '../../api';
import { openTable, patchExplorer, toggleExpanded, useExplorer } from '../../explorerState';
import { useSettings } from '../../settings';
import { t } from '../../i18n';
import { ConfirmDialog, Icon, IconButton, Menu, toast, type MenuItem } from '../../kit';
import { CreateDbDialog } from './dialogs';

type MenuState = { x: number; y: number; items: MenuItem[] } | null;

/**
 * HeidiSQL-style tree: databases expand into their tables (loaded when expanded, then cached).
 * Keyboard: Up/Down move, Right/Left expand/collapse, Enter opens.
 */
export function SchemaTree({ inst, filter }: { inst: DbInstance; filter: string }) {
  const dbs = databasesCache.use([inst]);
  const st = useExplorer(inst.id);
  const [settings] = useSettings();
  const [menu, setMenu] = useState<MenuState>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [dropDb, setDropDb] = useState<string | null>(null);

  // First visit: open the first user database.
  useEffect(() => {
    if (st.db || !dbs.data?.length) return;
    const first = dbs.data.find((d) => !isSystemDb(d.name)) ?? dbs.data[0];
    patchExplorer(inst.id, (s) => ({ db: first.name, expanded: s.expanded.includes(first.name) ? s.expanded : [...s.expanded, first.name] }));
  }, [dbs.data, st.db, inst.id]);

  const q = filter.trim().toLowerCase();
  const list = (dbs.data ?? []).filter((d) => settings.showSystemDbs || !isSystemDb(d.name) || d.name === st.db);

  const dbMenu = (e: MouseEvent, name: string) => {
    e.preventDefault();
    const items: MenuItem[] = [
      { id: 'query', label: t('New query here'), icon: 'code', onSelect: () => patchExplorer(inst.id, (s) => ({ db: name, table: name === s.db ? s.table : '', tab: 'query' })) },
      { id: 'refresh', label: t('Refresh tables'), icon: 'refresh', onSelect: () => void tablesCache.get([inst, name], true).catch((er) => toast.err(t('List tables failed'), er.message)) },
      { type: 'separator' },
      { id: 'new', label: t('New database…'), icon: 'plus', onSelect: () => setCreateOpen(true) },
    ];
    if (!isSystemDb(name) && inst.engine !== 'sqlite') {
      items.push({ id: 'drop', label: t('Drop database…'), icon: 'trash', danger: true, onSelect: () => setDropDb(name) });
    }
    setMenu({ x: e.clientX, y: e.clientY, items });
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const rows = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('[data-row]'));
    const i = rows.indexOf(document.activeElement as HTMLButtonElement);
    const cur = rows[i];
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
      next?.focus();
    } else if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && cur) {
      e.preventDefault();
      const db = cur.dataset.db!;
      if (cur.dataset.row === 'db') toggleExpanded(inst.id, db, e.key === 'ArrowRight');
      else if (e.key === 'ArrowLeft') rows.find((r) => r.dataset.row === 'db' && r.dataset.db === db)?.focus();
    }
  };

  const refreshAll = () => {
    invalidateInstance(inst, 'tables');
    void dbs.refresh();
  };

  return (
    <>
      <div className="db-nav-gl">
        {t('Databases')}
        {dbs.data && <span style={{ fontWeight: 600 }}>{list.length}</span>}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 2 }}>
          {inst.engine !== 'sqlite' && <IconButton icon="plus" label={t('New database…')} size="sm" onClick={() => setCreateOpen(true)} />}
          <IconButton icon="refresh" label={t('Refresh')} size="sm" loading={dbs.loading} onClick={refreshAll} />
        </span>
      </div>
      <div className="db-tree" role="tree" aria-label={t('Databases')} onKeyDown={onKey}>
        {!dbs.data && dbs.loading && <div className="db-tree-empty">{t('Loading…')}</div>}
        {dbs.error && !dbs.data && <div className="db-tree-empty" style={{ color: 'var(--err)' }}>{dbs.error}</div>}
        {list.map((d) => (
          <DbNode
            key={d.name}
            inst={inst}
            name={d.name}
            count={d.tables_count}
            filter={q}
            expanded={st.expanded.includes(d.name)}
            activeDb={st.db}
            activeTable={st.table}
            onMenu={dbMenu}
          />
        ))}
      </div>

      {menu && <Menu items={menu.items} anchor={{ x: menu.x, y: menu.y }} onClose={() => setMenu(null)} />}
      <CreateDbDialog inst={inst} open={createOpen} onClose={() => setCreateOpen(false)} />
      <ConfirmDialog
        open={!!dropDb}
        onClose={() => setDropDb(null)}
        onConfirm={async () => {
          if (!dropDb) return;
          try {
            await dbApi.dropDatabase(inst, dropDb);
            toast.ok(t('Database dropped'), dropDb);
            if (st.db === dropDb) patchExplorer(inst.id, { db: '', table: '' });
            invalidateInstance(inst, 'tables', dropDb);
            invalidateInstance(inst, 'dbs');
          } catch (err: any) {
            toast.err(t('Drop failed'), err.message);
          }
          setDropDb(null);
        }}
        title={t('Drop database?')}
        description={dropDb ? t('"{db}" and all its tables will be permanently deleted.', { db: dropDb }) : ''}
        confirmLabel={t('Drop database')}
        confirmText={dropDb || undefined}
        danger
        icon="trash"
      />
    </>
  );
}

function DbNode({ inst, name, count, filter, expanded, activeDb, activeTable, onMenu }: {
  inst: DbInstance;
  name: string;
  count?: number;
  filter: string;
  expanded: boolean;
  activeDb: string;
  activeTable: string;
  onMenu(e: MouseEvent, name: string): void;
}) {
  const tables = tablesCache.use(expanded || filter ? [inst, name] : null);
  const nameHit = !filter || name.toLowerCase().includes(filter);
  const shown: TableInfo[] = (tables.data ?? []).filter((x) => nameHit || x.name.toLowerCase().includes(filter));
  if (filter && !nameHit && shown.length === 0) return null;
  const open = expanded || (!!filter && shown.length > 0 && !nameHit);
  const sys = isSystemDb(name);

  return (
    <>
      <button
        type="button"
        data-row="db"
        data-db={name}
        role="treeitem"
        aria-expanded={open}
        className={`db-tree-row db-tree-row--db${sys ? ' db-tree-row--sys' : ''}${name === activeDb ? ' db-tree-row--active-db' : ''}`}
        onClick={() => {
          toggleExpanded(inst.id, name);
          if (name !== activeDb) patchExplorer(inst.id, { db: name, table: '' });
        }}
        onContextMenu={(e) => onMenu(e, name)}
        title={name}
      >
        <span className="db-tree-chev"><Icon name="db-chevright" /></span>
        <Icon name="db-database" />
        <span className="db-tree-n">{name}</span>
        {(tables.data?.length ?? count) !== undefined && <b>{tables.data?.length ?? count}</b>}
      </button>
      {open && (
        <>
          {!tables.data && tables.loading && <div className="db-tree-empty">{t('Loading…')}</div>}
          {tables.error && <div className="db-tree-empty" style={{ color: 'var(--err)' }}>{tables.error}</div>}
          {tables.data && shown.length === 0 && <div className="db-tree-empty">{t('No tables')}</div>}
          {shown.map((x) => {
            const view = /view/i.test(x.type || '');
            return (
              <button
                key={x.name}
                type="button"
                data-row="tb"
                data-db={name}
                role="treeitem"
                aria-current={name === activeDb && x.name === activeTable ? 'true' : undefined}
                className="db-tree-row db-tree-row--tb"
                onClick={() => openTable(inst.id, name, x.name)}
                onContextMenu={(e) => onMenu(e, name)}
                title={`${x.name}${view ? ' (view)' : ''}`}
              >
                <Icon name={view ? 'db-view' : 'db-table'} />
                <span className="db-tree-n">{x.name}</span>
                {!view && x.rows_count !== undefined && x.rows_count !== null && <b>{formatCount(x.rows_count)}</b>}
              </button>
            );
          })}
        </>
      )}
    </>
  );
}

export function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  return String(n);
}
