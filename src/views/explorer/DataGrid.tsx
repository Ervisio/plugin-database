import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent as RMouseEvent } from 'react';
import {
  browseCache, dbApi, invalidateInstance, invalidateTableData,
  type BrowseDataResult, type BrowseParams, type ColFilter, type ColumnMeta, type DbInstance, type FilterOp,
} from '../../api';
import { gridKey, newGrid, patchGrid, useExplorer, type GridState } from '../../explorerState';
import { useSettings } from '../../settings';
import { invalidate } from '../../store';
import { getSdk } from '../../sdk';
import { t } from '../../i18n';
import { Button, ConfirmDialog, DropdownMenu, Icon, IconButton, Input, Menu, Select, toast, type MenuItem } from '../../kit';
import { cellText, copyText, quoteIdent, sqlLiteral, toCsv, toInsert, toJson } from '../../utils';
import { RowDialog } from './dialogs';

const OPS: { value: FilterOp; label: string; title: string }[] = [
  { value: 'contains', label: '~', title: 'contains' },
  { value: 'eq', label: '=', title: 'equals' },
  { value: 'ne', label: '≠', title: 'not equal' },
  { value: 'gt', label: '>', title: 'greater than' },
  { value: 'gte', label: '≥', title: 'greater or equal' },
  { value: 'lt', label: '<', title: 'less than' },
  { value: 'lte', label: '≤', title: 'less or equal' },
  { value: 'starts', label: 'a…', title: 'starts with' },
  { value: 'ends', label: '…a', title: 'ends with' },
  { value: 'null', label: '∅', title: 'is NULL' },
  { value: 'notnull', label: '!∅', title: 'is not NULL' },
];
const OP_TEXT: Record<FilterOp, string> = { contains: '~', eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤', starts: 'starts', ends: 'ends', null: 'IS NULL', notnull: 'IS NOT NULL' };
const NO_VALUE = (op: FilterOp) => op === 'null' || op === 'notnull';

const NUM_RE = /int|decimal|numeric|float|double|real|serial|money/i;
const isBlob = (v: unknown) => typeof v === 'string' && v.startsWith('<blob ') && v.endsWith(' bytes>');

function defaultWidth(name: string, m?: ColumnMeta): number {
  const type = (m?.type || '').toLowerCase();
  // Room for the name, the type label and the key icon.
  const byName = Math.min(340, Math.max(90, name.length * 8 + type.length * 6.5 + (m?.pk ? 64 : 48)));
  if (NUM_RE.test(type)) return Math.max(byName, 90);
  if (/date|time/.test(type)) return Math.max(byName, 170);
  if (/text|json|char|blob/.test(type)) return Math.max(byName, 200);
  return Math.max(byName, 140);
}

type Cell = { r: number; c: number };

export function DataGrid({ inst, db, table }: { inst: DbInstance; db: string; table: string }) {
  const [settings] = useSettings();
  const st = useExplorer(inst.id);
  const key = gridKey(db, table);
  const gs: GridState = st.grids[key] ?? newGrid(settings.perPage);
  const patch = (p: Partial<GridState>) => patchGrid(inst.id, key, settings.perPage, p);

  const params: BrowseParams = useMemo(() => ({
    database: db,
    table,
    page: gs.page,
    per_page: gs.perPage,
    sort_col: gs.sortCol || undefined,
    sort_dir: gs.sortDir,
    search: gs.search || undefined,
    col_filters: gs.colFilters.length ? gs.colFilters : undefined,
    filter: gs.where || undefined,
  }), [db, table, gs.page, gs.perPage, gs.sortCol, gs.sortDir, gs.search, gs.colFilters, gs.where]);

  const res = browseCache.use([inst, params]);
  // Keep the previous page on screen while the next one loads (same table only).
  const last = useRef<{ key: string; data: BrowseDataResult } | null>(null);
  if (res.data) last.current = { key, data: res.data };
  const data = res.data ?? (last.current?.key === key ? last.current.data : undefined);

  const cols = data?.columns ?? [];
  const meta = data?.meta ?? [];
  const metaBy = useMemo(() => Object.fromEntries(meta.map((m) => [m.name, m])), [meta]);
  const pks = data?.primary_keys ?? [];
  const editable = pks.length > 0;
  const rows = data?.rows ?? [];

  /* ---------- quick search: live (debounced, from N chars) or Enter ---------- */
  useEffect(() => {
    if (!settings.liveSearch || gs.searchInput === gs.search) return;
    const v = gs.searchInput.trim();
    if (v.length > 0 && v.length < settings.liveSearchMin) return;
    const h = setTimeout(() => patch({ search: v, page: 1 }), settings.liveSearchDelay);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gs.searchInput, settings.liveSearch, settings.liveSearchMin, settings.liveSearchDelay]);

  /* ---------- per-column filter drafts ---------- */
  const [drafts, setDrafts] = useState<Record<string, { op: FilterOp; value: string }>>({});
  useEffect(() => {
    setDrafts(Object.fromEntries(gs.colFilters.map((f) => [f.col, { op: f.op, value: f.value ?? '' }])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const commitFilters = (d = drafts) => {
    const next: ColFilter[] = Object.entries(d)
      .filter(([, f]) => NO_VALUE(f.op) || f.value.trim() !== '')
      .map(([col, f]) => (NO_VALUE(f.op) ? { col, op: f.op } : { col, op: f.op, value: f.value }));
    if (JSON.stringify(next) !== JSON.stringify(gs.colFilters)) patch({ colFilters: next, page: 1 });
  };
  useEffect(() => {
    if (!settings.liveSearch) return;
    const tooShort = Object.values(drafts).some((f) => !NO_VALUE(f.op) && f.value.trim().length > 0 && f.value.trim().length < settings.liveSearchMin && f.op === 'contains');
    if (tooShort) return;
    const h = setTimeout(() => commitFilters(), settings.liveSearchDelay);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drafts]);

  /* ---------- selection, focus, editing ---------- */
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [focus, setFocus] = useState<Cell | null>(null);
  const [edit, setEdit] = useState<(Cell & { draft: string }) | null>(null);
  const [cellState, setCellState] = useState<Record<string, 'saving' | 'flash' | 'err'>>({});
  const committing = useRef(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [rowDialog, setRowDialog] = useState<{ initial: Record<string, any> | null } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<number[] | null>(null);

  // New page, filter or reload: clear selection and editing (optimistic cell updates keep them).
  const viewKey = `${browseCache.key([inst, params])}@${res.at}`;
  useEffect(() => {
    setSelected(new Set());
    setEdit(null);
    setFocus(null);
  }, [viewKey]);

  const ck = (r: number, c: string) => `${r}:${c}`;
  const pkOf = (row: Record<string, any>) => Object.fromEntries(pks.map((k) => [k, row[k]]));
  const canEdit = (row: Record<string, any>, col: string) => editable && !isBlob(row[col]);

  const startEdit = (r: number, c: number) => {
    const col = cols[c];
    const row = rows[r];
    if (!row || !col) return;
    if (!editable) {
      toast.info(t('Read-only table'), t('Editing needs a primary key.'));
      return;
    }
    if (!canEdit(row, col)) return;
    committing.current = false;
    setFocus({ r, c });
    setEdit({ r, c, draft: cellText(row[col]) });
  };

  const saveCell = async (r: number, col: string, value: string | null) => {
    const row = rows[r];
    if (!row) return;
    const old = row[col];
    if (value !== null && old !== null && cellText(old) === value) return;
    if (value === null && old === null) return;
    const k = ck(r, col);
    const where = pkOf(row);
    setCellState((s) => ({ ...s, [k]: 'saving' }));
    browseCache.mutate([inst, params], (cur) => cur && { ...cur, rows: cur.rows.map((x, i) => (i === r ? { ...x, [col]: value } : x)) });
    try {
      const out = await dbApi.saveRow(inst, { database: db, table, data: { [col]: value }, pks: where });
      if (out.affected === 0) toast.warn(t('No row changed'), t('The row may have been changed or deleted by someone else.'));
      setCellState((s) => ({ ...s, [k]: 'flash' }));
      setTimeout(() => setCellState((s) => {
        const n = { ...s };
        if (n[k] === 'flash') delete n[k];
        return n;
      }), 1200);
      // Other pages/filters of this table are now outdated; the current one is up to date.
      invalidateOthers();
    } catch (err: any) {
      browseCache.mutate([inst, params], (cur) => cur && { ...cur, rows: cur.rows.map((x, i) => (i === r ? { ...x, [col]: old } : x)) });
      setCellState((s) => ({ ...s, [k]: 'err' }));
      toast.err(t('Save failed'), err.message);
    }
  };
  const invalidateOthers = () => {
    const mine = browseCache.key([inst, params]);
    const prefix = `browse:${inst.id}|${db}|${table}|`;
    // Drop every cached page of this table except the one on screen.
    invalidate((k) => k.startsWith(prefix) && k !== mine);
  };

  const finishEdit = (move?: 'next' | 'prev' | 'down') => {
    if (!edit || committing.current) return;
    committing.current = true;
    const { r, c, draft } = edit;
    setEdit(null);
    void saveCell(r, cols[c], draft);
    if (move) {
      let nr = r;
      let nc = c + (move === 'next' ? 1 : move === 'prev' ? -1 : 0);
      if (move === 'down') nr = r + 1;
      if (nc >= cols.length) { nc = 0; nr = r + 1; }
      if (nc < 0) { nc = cols.length - 1; nr = r - 1; }
      if (nr >= 0 && nr < rows.length) {
        if (move === 'down') setFocus({ r: nr, c: nc });
        else setTimeout(() => startEdit(nr, nc), 0);
      }
    }
    setTimeout(() => wrapRef.current?.focus({ preventScroll: true }), 0);
  };

  const cancelEdit = () => {
    committing.current = true;
    setEdit(null);
    setTimeout(() => wrapRef.current?.focus({ preventScroll: true }), 0);
  };

  const onGridKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (edit || !focus || e.target !== e.currentTarget) return;
    const { r, c } = focus;
    const move = (dr: number, dc: number) => {
      e.preventDefault();
      setFocus({ r: Math.max(0, Math.min(rows.length - 1, r + dr)), c: Math.max(0, Math.min(cols.length - 1, c + dc)) });
    };
    if (e.key === 'ArrowDown') move(1, 0);
    else if (e.key === 'ArrowUp') move(-1, 0);
    else if (e.key === 'ArrowRight' || (e.key === 'Tab' && !e.shiftKey)) move(0, 1);
    else if (e.key === 'ArrowLeft' || (e.key === 'Tab' && e.shiftKey)) move(0, -1);
    else if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); startEdit(r, c); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c') { e.preventDefault(); void copy(cellText(rows[r]?.[cols[c]])); }
    else if (e.key === 'Delete' && editable && selected.size > 0) { e.preventDefault(); askDelete([...selected]); }
    else if (e.key === 'Escape') setFocus(null);
  };

  // Keep the focused cell visible.
  useEffect(() => {
    if (!focus) return;
    wrapRef.current?.querySelector<HTMLElement>(`[data-cell="${focus.r}:${focus.c}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [focus]);

  /* ---------- actions ---------- */
  const copy = async (text: string, what = t('Copied')) => {
    const ok = await copyText(text);
    if (ok) toast.ok(what);
    else toast.warn(t('Copy blocked'), t('The frame denied clipboard access'));
  };

  const askDelete = (idx: number[]) => {
    if (!editable || idx.length === 0) return;
    if (settings.confirmDelete) setPendingDelete(idx);
    else void doDelete(idx);
  };

  const doDelete = async (idx: number[]) => {
    try {
      const out = await dbApi.deleteRows(inst, { database: db, table, pks_list: idx.map((i) => pkOf(rows[i])) });
      toast.ok(out.affected === 1 ? t('Row deleted') : t('{n} rows deleted', { n: out.affected }), table);
      invalidateTableData(inst, db, table);
      invalidateInstance(inst, 'tables', db);
    } catch (err: any) {
      toast.err(t('Delete failed'), err.message);
    }
  };

  const addFilter = (col: string, value: any) => {
    const f: ColFilter = value === null ? { col, op: 'null' } : { col, op: 'eq', value: cellText(value) };
    const next = [...gs.colFilters.filter((x) => x.col !== col), f];
    setDrafts((d) => ({ ...d, [col]: { op: f.op, value: f.value ?? '' } }));
    patch({ colFilters: next, page: 1 });
  };

  const exportRows = async (fmt: 'csv' | 'json' | 'sql', which: Record<string, any>[]) => {
    if (!which.length) {
      toast.warn(t('Nothing to export'));
      return;
    }
    const body = fmt === 'csv' ? toCsv(cols, which) : fmt === 'json' ? toJson(cols, which) : toInsert(inst.engine, table, cols, which);
    const mime = fmt === 'csv' ? 'text/csv' : fmt === 'json' ? 'application/json' : 'application/sql';
    try {
      await getSdk().saveFile(`${table}.${fmt}`, body, mime);
      toast.ok(t('Exported'), t('{n} rows', { n: which.length }));
    } catch (err: any) {
      toast.err(t('Export failed'), err.message);
    }
  };

  const cellMenu = (e: RMouseEvent, r: number, c: number) => {
    e.preventDefault();
    const row = rows[r];
    const col = cols[c];
    setFocus({ r, c });
    const m = metaBy[col];
    const targets = selected.size > 0 && selected.has(r) ? [...selected] : [r];
    const items: MenuItem[] = [
      { id: 'edit', label: t('Edit cell'), icon: 'edit', kbd: 'F2', disabled: !canEdit(row, col), onSelect: () => startEdit(r, c) },
      { id: 'null', label: t('Set NULL'), icon: 'minus', disabled: !canEdit(row, col) || !m?.nullable || row[col] === null, onSelect: () => void saveCell(r, col, null) },
      { type: 'separator' },
      { id: 'copy', label: t('Copy value'), icon: 'copy', kbd: 'Ctrl+C', onSelect: () => void copy(cellText(row[col])) },
      { id: 'copyjson', label: t('Copy row as JSON'), icon: 'code', onSelect: () => void copy(JSON.stringify(row, null, 2), t('Row copied')) },
      { id: 'copysql', label: t('Copy as INSERT'), icon: 'code', onSelect: () => void copy(toInsert(inst.engine, table, cols, targets.map((i) => rows[i])).trim(), t('Copied')) },
      { id: 'where', label: t('Copy WHERE for this row'), icon: 'filter', disabled: !editable, onSelect: () => void copy(pks.map((k) => `${quoteIdent(inst.engine, k)} = ${sqlLiteral(row[k], inst.engine)}`).join(' AND ')) },
      { type: 'separator' },
      { id: 'filter', label: t('Filter by this value'), icon: 'filter', onSelect: () => addFilter(col, row[col]) },
      { id: 'dup', label: t('Duplicate row…'), icon: 'plus', onSelect: () => setRowDialog({ initial: row }) },
      { type: 'separator' },
      { id: 'del', label: targets.length > 1 ? t('Delete {n} rows', { n: targets.length }) : t('Delete row'), icon: 'trash', danger: true, disabled: !editable, onSelect: () => askDelete(targets) },
    ];
    setMenu({ x: e.clientX, y: e.clientY, items });
  };

  /* ---------- column resize ---------- */
  const [resizing, setResizing] = useState<string | null>(null);
  const widthOf = (c: string) => gs.widths[c] ?? defaultWidth(c, metaBy[c]);
  const startResize = (e: RMouseEvent, col: string) => {
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX;
    const w0 = widthOf(col);
    setResizing(col);
    let w = w0;
    const onMove = (ev: MouseEvent) => {
      w = Math.max(50, Math.min(900, w0 + ev.clientX - x0));
      const th = wrapRef.current?.querySelector<HTMLElement>(`col[data-col="${CSS.escape(col)}"]`);
      if (th) th.style.width = `${w}px`;
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setResizing(null);
      patch({ widths: { ...gs.widths, [col]: w } });
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const sortBy = (c: string) => {
    if (gs.sortCol !== c) patch({ sortCol: c, sortDir: 'ASC', page: 1 });
    else if (gs.sortDir === 'ASC') patch({ sortDir: 'DESC', page: 1 });
    else patch({ sortCol: '', sortDir: 'ASC', page: 1 });
  };

  const activeFilters = (gs.search ? 1 : 0) + gs.colFilters.length + (gs.where ? 1 : 0);
  const clearAll = () => {
    setDrafts({});
    patch({ search: '', searchInput: '', colFilters: [], where: '', whereInput: '', page: 1 });
  };
  const allSelected = rows.length > 0 && selected.size === rows.length;
  const totalPages = data?.total_pages ?? 1;
  const tableWidth = 38 + cols.reduce((n, c) => n + widthOf(c), 0);

  return (
    <div className="db-col" style={{ gap: 10 }}>
      <div className="db-tb">
        <div className="db-tb-search">
          <Input
            compact
            icon="search"
            value={gs.searchInput}
            placeholder={settings.liveSearch ? t('Search all columns…') : t('Search all columns… (Enter)')}
            aria-label={t('Search all columns')}
            onChange={(e) => patch({ searchInput: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter') patch({ search: gs.searchInput.trim(), page: 1 });
              if (e.key === 'Escape' && gs.searchInput) patch({ searchInput: '', search: '', page: 1 });
            }}
            end={res.loading && data ? <Icon name="refresh" className="db-spin" /> : undefined}
          />
        </div>
        <Button size="sm" variant={gs.showColFilters ? 'primary' : 'secondary'} icon="filter" onClick={() => patch({ showColFilters: !gs.showColFilters })}>
          {t('Column filters')}
        </Button>
        <Button size="sm" variant={gs.showWhere ? 'primary' : 'secondary'} icon="code" onClick={() => patch({ showWhere: !gs.showWhere })}>
          WHERE
        </Button>
        <span className="db-tb-sep" />
        {editable && selected.size > 0 && (
          <Button size="sm" variant="danger" icon="trash" onClick={() => askDelete([...selected])}>
            {t('Delete {n}', { n: selected.size })}
          </Button>
        )}
        <Button size="sm" variant="primary" icon="plus" disabled={!meta.length} onClick={() => setRowDialog({ initial: null })}>{t('Insert row')}</Button>
        <DropdownMenu
          aria-label={t('Export')}
          items={[
            { type: 'heading', label: selected.size ? t('Selected rows') : t('This page') },
            { id: 'csv', label: 'CSV', icon: 'download', onSelect: () => void exportRows('csv', selected.size ? [...selected].sort((a, b) => a - b).map((i) => rows[i]) : rows) },
            { id: 'json', label: 'JSON', icon: 'download', onSelect: () => void exportRows('json', selected.size ? [...selected].sort((a, b) => a - b).map((i) => rows[i]) : rows) },
            { id: 'sql', label: 'SQL INSERT', icon: 'download', onSelect: () => void exportRows('sql', selected.size ? [...selected].sort((a, b) => a - b).map((i) => rows[i]) : rows) },
          ]}
          trigger={(p) => <Button size="sm" variant="secondary" icon="download" {...(p as any)}>{t('Export')}</Button>}
        />
        <IconButton icon="refresh" label={t('Reload')} loading={res.loading} onClick={() => { invalidateTableData(inst, db, table); }} />
      </div>

      {gs.showWhere && (
        <div className="db-where">
          <Input
            compact
            mono
            icon="code"
            value={gs.whereInput}
            placeholder={t("Raw SQL condition, e.g. id > 10 AND status = 'active' (Enter)")}
            aria-label="WHERE"
            onChange={(e) => patch({ whereInput: e.target.value })}
            onKeyDown={(e) => { if (e.key === 'Enter') patch({ where: gs.whereInput.trim(), page: 1 }); }}
          />
          <Button size="sm" variant="secondary" onClick={() => patch({ where: gs.whereInput.trim(), page: 1 })}>{t('Apply')}</Button>
        </div>
      )}

      {activeFilters > 0 && (
        <div className="db-chips">
          {gs.search && <Chip label={`“${gs.search}”`} onRemove={() => patch({ search: '', searchInput: '', page: 1 })} />}
          {gs.colFilters.map((f) => (
            <Chip
              key={f.col}
              label={`${f.col} ${OP_TEXT[f.op]}${NO_VALUE(f.op) ? '' : ` ${f.value}`}`}
              onRemove={() => {
                setDrafts((d) => {
                  const n = { ...d };
                  delete n[f.col];
                  return n;
                });
                patch({ colFilters: gs.colFilters.filter((x) => x.col !== f.col), page: 1 });
              }}
            />
          ))}
          {gs.where && <Chip label={`WHERE ${gs.where}`} onRemove={() => patch({ where: '', page: 1 })} />}
          {activeFilters > 1 && <button type="button" className="db-chip db-chip--clear" onClick={clearAll}>{t('Clear all')}</button>}
        </div>
      )}

      {!editable && data && (
        <div className="db-note db-note--warn">
          <Icon name="lock" />
          <span>{t('This table has no primary key, so it is read-only here: an edit could change more than one row. Use the Query tab to change it.')}</span>
        </div>
      )}

      {res.error && !data && <div className="db-qerr">{res.error}</div>}
      {res.error && data && <div className="db-qerr">{res.error}</div>}

      <div
        ref={wrapRef}
        className="db-gridwrap"
        tabIndex={0}
        onKeyDown={onGridKey}
        aria-label={t('Rows of {table}', { table })}
        style={resizing ? { cursor: 'col-resize', userSelect: 'none' } : undefined}
      >
        {!data && res.loading ? (
          <div className="db-empty-pane">{t('Loading…')}</div>
        ) : cols.length === 0 ? (
          <div className="db-empty-pane">{t('No columns')}</div>
        ) : (
          <table className={`db-dg${editable ? '' : ' db-dg--ro'}`} style={{ width: tableWidth }}>
            <colgroup>
              <col style={{ width: 38 }} />
              {cols.map((c) => <col key={c} data-col={c} style={{ width: widthOf(c) }} />)}
            </colgroup>
            <thead>
              <tr>
                <th className="db-cb">
                  <input
                    type="checkbox"
                    aria-label={t('Select all rows')}
                    checked={allSelected}
                    ref={(el) => { if (el) el.indeterminate = selected.size > 0 && !allSelected; }}
                    onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((_, i) => i)))}
                  />
                </th>
                {cols.map((c) => {
                  const m = metaBy[c];
                  return (
                    <th key={c} title={m ? `${c} · ${m.type}${m.pk ? ' · PRIMARY KEY' : ''}${m.nullable ? ' · NULL' : ''}` : c} aria-sort={gs.sortCol === c ? (gs.sortDir === 'ASC' ? 'ascending' : 'descending') : undefined}>
                      <div className="db-th" onClick={() => sortBy(c)}>
                        {m?.pk && <Icon name="db-key" />}
                        <span>{c}</span>
                        {gs.sortCol === c && <i>{gs.sortDir === 'ASC' ? '▲' : '▼'}</i>}
                        {m && <small>{m.type}</small>}
                      </div>
                      <span className={`db-rs${resizing === c ? ' db-rs--on' : ''}`} onMouseDown={(e) => startResize(e, c)} onClick={(e) => e.stopPropagation()} />
                    </th>
                  );
                })}
              </tr>
              {gs.showColFilters && (
                <tr className="db-fr">
                  <th />
                  {cols.map((c) => {
                    const d = drafts[c] ?? { op: 'contains' as FilterOp, value: '' };
                    const set = (nd: { op: FilterOp; value: string }) => {
                      const next = { ...drafts, [c]: nd };
                      setDrafts(next);
                      if (NO_VALUE(nd.op) || (NO_VALUE(d.op) && !nd.value)) commitFilters(next);
                    };
                    return (
                      <th key={c}>
                        <div>
                          <select aria-label={t('Operator for {col}', { col: c })} value={d.op} title={OPS.find((o) => o.value === d.op)?.title} onChange={(e) => set({ op: e.target.value as FilterOp, value: d.value })}>
                            {OPS.map((o) => <option key={o.value} value={o.value} title={o.title}>{o.label}</option>)}
                          </select>
                          {!NO_VALUE(d.op) && (
                            <input
                              aria-label={t('Filter {col}', { col: c })}
                              value={d.value}
                              placeholder="…"
                              onChange={(e) => set({ op: d.op, value: e.target.value })}
                              onKeyDown={(e) => { if (e.key === 'Enter') commitFilters(); }}
                            />
                          )}
                        </div>
                      </th>
                    );
                  })}
                </tr>
              )}
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={cols.length + 1} style={{ textAlign: 'center', padding: 28, fontFamily: 'inherit' }} className="db-muted">
                  {activeFilters ? t('No rows match the filters') : t('This table is empty')}
                </td></tr>
              )}
              {rows.map((row, r) => (
                <tr key={r} className={selected.has(r) ? 'db-sel' : undefined}>
                  <td className="db-cb">
                    <input
                      type="checkbox"
                      aria-label={t('Select row {n}', { n: r + 1 })}
                      checked={selected.has(r)}
                      onChange={() => setSelected((s) => {
                        const n = new Set(s);
                        if (n.has(r)) n.delete(r);
                        else n.add(r);
                        return n;
                      })}
                    />
                  </td>
                  {cols.map((col, c) => {
                    const v = row[col];
                    const k = ck(r, col);
                    const isEdit = edit && edit.r === r && edit.c === c;
                    const isFocus = !isEdit && focus && focus.r === r && focus.c === c;
                    const cls = [
                      NUM_RE.test(metaBy[col]?.type || '') ? 'db-num' : '',
                      isEdit ? 'db-cell--edit' : '',
                      isFocus ? 'db-cell--focus' : '',
                      cellState[k] ? `db-cell--${cellState[k]}` : '',
                      canEdit(row, col) ? '' : 'db-ro',
                    ].filter(Boolean).join(' ');
                    return (
                      <td
                        key={col}
                        data-cell={`${r}:${c}`}
                        className={cls || undefined}
                        title={isEdit ? undefined : cellText(v).slice(0, 500)}
                        onClick={() => { if (!isEdit) setFocus({ r, c }); }}
                        onDoubleClick={() => startEdit(r, c)}
                        onContextMenu={(e) => cellMenu(e, r, c)}
                      >
                        {isEdit ? (
                          <input
                            autoFocus
                            value={edit!.draft}
                            aria-label={t('Edit {col}', { col })}
                            onChange={(e) => setEdit({ ...edit!, draft: e.target.value })}
                            onFocus={(e) => e.currentTarget.select()}
                            onBlur={() => finishEdit()}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') { e.preventDefault(); finishEdit('down'); }
                              else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
                              else if (e.key === 'Tab') { e.preventDefault(); finishEdit(e.shiftKey ? 'prev' : 'next'); }
                            }}
                          />
                        ) : v === null ? (
                          <span className="db-null">{settings.nullText || 'NULL'}</span>
                        ) : (
                          cellText(v)
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="db-foot">
        <span className="db-muted">
          {data ? t('{total} rows', { total: data.total.toLocaleString() }) : ''}
          {editable && data ? ` · ${t('double-click a cell to edit')}` : ''}
        </span>
        <div className="db-row">
          <IconButton icon="chevronleft" label={t('Previous page')} size="sm" disabled={gs.page <= 1} onClick={() => patch({ page: gs.page - 1 })} />
          <span className="db-muted">{t('Page {page} of {pages}', { page: gs.page, pages: totalPages })}</span>
          <IconButton icon="db-chevright" label={t('Next page')} size="sm" disabled={gs.page >= totalPages} onClick={() => patch({ page: gs.page + 1 })} />
          <Select
            compact
            value={String(gs.perPage)}
            onChange={(v) => patch({ perPage: parseInt(v, 10), page: 1 })}
            options={[25, 50, 100, 250, 500, 1000].map((n) => ({ value: String(n), label: t('{n} / page', { n }) }))}
          />
        </div>
      </div>

      {menu && <Menu items={menu.items} anchor={{ x: menu.x, y: menu.y }} onClose={() => setMenu(null)} />}
      <RowDialog
        inst={inst}
        db={db}
        table={table}
        meta={meta}
        initial={rowDialog?.initial ?? null}
        open={!!rowDialog}
        onClose={() => setRowDialog(null)}
        onSaved={() => invalidateTableData(inst, db, table)}
      />
      <ConfirmDialog
        open={!!pendingDelete}
        onClose={() => setPendingDelete(null)}
        onConfirm={async () => {
          const idx = pendingDelete ?? [];
          setPendingDelete(null);
          await doDelete(idx);
        }}
        title={pendingDelete && pendingDelete.length > 1 ? t('Delete {n} rows?', { n: pendingDelete.length }) : t('Delete row?')}
        description={t('This cannot be undone. You can turn this confirmation off in Settings.')}
        confirmLabel={t('Delete')}
        danger
        icon="trash"
      />
    </div>
  );
}

function Chip({ label, onRemove }: { label: string; onRemove(): void }) {
  return (
    <span className="db-chip" title={label}>
      <span>{label}</span>
      <button type="button" aria-label={t('Remove filter')} onClick={onRemove}><Icon name="close" /></button>
    </span>
  );
}
