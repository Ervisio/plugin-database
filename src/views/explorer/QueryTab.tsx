import { useEffect, useMemo, useRef, useState } from 'react';
import type { SQLNamespace } from '@codemirror/lang-sql';
import { databasesCache, dbApi, invalidateInstance, structureCache, tablesCache, type DbInstance, type QueryResult } from '../../api';
import { invalidate } from '../../store';
import { ensureFile, pushHistory, setFile, useFile, useSettings, type QueryTabState } from '../../settings';
import { getSdk } from '../../sdk';
import { t } from '../../i18n';
import { Button, Dialog, DropdownMenu, Icon, IconButton, Input, Kbd, Select, toast } from '../../kit';
import { cellText, quoteIdent, timeAgo, toCsv, toInsert, toJson } from '../../utils';
import { SqlEditor, statementRanges, type SqlEditorApi } from './SqlEditor';

interface RunState {
  result?: QueryResult;
  error?: string;
  sql?: string;
  limited?: number;
  running?: boolean;
}

/** Results per instance + tab, kept while the plugin is open so coming back shows the last result. */
const results = new Map<string, RunState>();

const newTab = (n: number): QueryTabState => ({ id: `q${Date.now().toString(36)}${n}`, title: `Query ${n}`, sql: '' });

export function QueryTab({ inst, db, table, onDbChange }: { inst: DbInstance; db: string; table: string; onDbChange(db: string): void }) {
  const [settings] = useSettings();
  const [file, , loaded] = useFile('query-tabs');
  const [history] = useFile('history');
  const saved = file.byInstance[inst.id];

  const [tabs, setTabs] = useState<QueryTabState[]>(() => saved?.tabs?.length ? saved.tabs : [newTab(1)]);
  const [active, setActive] = useState<string>(() => saved?.active ?? tabs[0].id);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [histOpen, setHistOpen] = useState(false);
  const [histQ, setHistQ] = useState('');
  const [, force] = useState(0);
  const editor = useRef<SqlEditorApi | null>(null);
  const hydrated = useRef(!!saved);

  // Tabs file arrives after the first render: adopt it once.
  useEffect(() => {
    if (!loaded || hydrated.current) return;
    hydrated.current = true;
    const s = file.byInstance[inst.id];
    if (s?.tabs?.length) {
      setTabs(s.tabs);
      setActive(s.active && s.tabs.some((x) => x.id === s.active) ? s.active : s.tabs[0].id);
    }
  }, [loaded, file, inst.id]);

  // Save tabs (debounced: every keystroke changes them).
  useEffect(() => {
    if (!hydrated.current && !loaded) return;
    const h = setTimeout(async () => {
      const cur = await ensureFile('query-tabs');
      void setFile('query-tabs', { byInstance: { ...cur.byInstance, [inst.id]: { tabs, active } } });
    }, 700);
    return () => clearTimeout(h);
  }, [tabs, active, inst.id, loaded]);

  const tab = tabs.find((x) => x.id === active) ?? tabs[0];
  const rkey = `${inst.id}|${tab.id}`;
  const run = results.get(rkey) ?? {};
  const setRun = (r: RunState) => {
    results.set(rkey, r);
    force((n) => n + 1);
  };

  /* ---------- autocomplete schema: tables of the current database, columns when known ---------- */
  const dbs = databasesCache.use([inst]);
  const tbls = tablesCache.use(db ? [inst, db] : null);
  const schema: SQLNamespace = useMemo(() => {
    const ns: Record<string, string[]> = {};
    for (const x of tbls.data ?? []) {
      const s = structureCache.peek([inst, db, x.name]);
      ns[x.name] = s ? s.columns.map((c) => c.name) : [];
    }
    return ns;
  }, [tbls.data, inst, db]);
  // Columns of the open table for better completion.
  structureCache.use(db && table ? [inst, db, table] : null);

  const updateSql = (sql: string) => setTabs((ts) => ts.map((x) => (x.id === tab.id ? { ...x, sql } : x)));

  const execute = async (text: string) => {
    let sqlText = text.trim();
    if (!sqlText) {
      toast.info(t('Nothing to run'), t('Write a query first.'));
      return;
    }
    const stmts = statementRanges(sqlText);
    let limited: number | undefined;
    if (settings.autoLimit > 0 && stmts.length === 1 && /^\s*(select|with)\b/i.test(stmts[0].sql) && !/\blimit\s+\d+/i.test(stmts[0].sql)) {
      sqlText = `${stmts[0].sql.replace(/;\s*$/, '')} LIMIT ${settings.autoLimit}`;
      limited = settings.autoLimit;
    }
    setRun({ ...run, running: true });
    const started = Date.now();
    try {
      const res = await dbApi.query(inst, { database: db || undefined, sql: sqlText });
      setRun({ result: res, sql: sqlText, limited });
      void pushHistory({ sql: text.trim(), at: started, instance: inst.name, ms: res.time_ms });
      const readOnly = stmts.every((s) => /^\s*(select|with|show|describe|desc|explain|pragma)\b/i.test(s.sql));
      if (!readOnly) {
        invalidate((k) => k.startsWith(`browse:${inst.id}|`) || k.startsWith(`struct:${inst.id}|`));
        invalidateInstance(inst, 'tables');
        if (stmts.some((s) => /\b(database|schema)\b/i.test(s.sql))) invalidateInstance(inst, 'dbs');
        if (stmts.some((s) => /\b(user|grant|revoke)\b/i.test(s.sql))) invalidateInstance(inst, 'users');
      }
    } catch (err: any) {
      setRun({ error: err.message, sql: sqlText });
    }
  };

  const runCurrent = () => void execute(editor.current?.current() ?? tab.sql);
  const runAll = () => void execute(editor.current?.all() ?? tab.sql);

  const addTab = () => {
    const nt = newTab(tabs.length + 1);
    setTabs([...tabs, nt]);
    setActive(nt.id);
  };
  const closeTab = (id: string) => {
    results.delete(`${inst.id}|${id}`);
    const rest = tabs.filter((x) => x.id !== id);
    const next = rest.length ? rest : [newTab(1)];
    setTabs(next);
    if (id === active) setActive(next[Math.max(0, tabs.findIndex((x) => x.id === id) - 1)]?.id ?? next[0].id);
  };

  const q = (n: string) => quoteIdent(inst.engine, n);
  const tpl = table || 'table_name';
  const templates = [
    { id: 'select', label: 'SELECT', sql: `SELECT *\nFROM ${q(tpl)}\nWHERE 1 = 1\nLIMIT 100;` },
    { id: 'count', label: 'COUNT', sql: `SELECT COUNT(*) FROM ${q(tpl)};` },
    { id: 'insert', label: 'INSERT', sql: `INSERT INTO ${q(tpl)} (column1, column2)\nVALUES ('value1', 'value2');` },
    { id: 'update', label: 'UPDATE', sql: `UPDATE ${q(tpl)}\nSET column1 = 'value'\nWHERE id = 1;` },
    { id: 'delete', label: 'DELETE', sql: `DELETE FROM ${q(tpl)}\nWHERE id = 1;` },
  ];

  const hist = history.items.filter((h) => !histQ || h.sql.toLowerCase().includes(histQ.toLowerCase()));

  const exportResult = async (fmt: 'csv' | 'json' | 'sql') => {
    const r = run.result;
    if (!r || !r.rows.length) return;
    const body = fmt === 'csv' ? toCsv(r.columns, r.rows) : fmt === 'json' ? toJson(r.columns, r.rows) : toInsert(inst.engine, table || 'result', r.columns, r.rows);
    try {
      await getSdk().saveFile(`query.${fmt}`, body, fmt === 'csv' ? 'text/csv' : fmt === 'json' ? 'application/json' : 'application/sql');
      toast.ok(t('Exported'), t('{n} rows', { n: r.rows.length }));
    } catch (err: any) {
      toast.err(t('Export failed'), err.message);
    }
  };

  return (
    <div className="db-col" style={{ gap: 10 }}>
      <div className="db-qtabs" role="tablist" aria-label={t('Query tabs')}>
        {tabs.map((x) => (
          <div
            key={x.id}
            role="tab"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) setActive(x.id); }}
            className="db-qtab"
            aria-selected={x.id === tab.id}
            onClick={() => setActive(x.id)}
            onDoubleClick={() => setRenaming(x.id)}
            title={t('Double-click to rename')}
          >
            {renaming === x.id ? (
              <input
                autoFocus
                defaultValue={x.title}
                onClick={(e) => e.stopPropagation()}
                onBlur={(e) => { const v = e.target.value.trim(); setTabs(tabs.map((y) => (y.id === x.id ? { ...y, title: v || y.title } : y))); setRenaming(null); }}
                onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setRenaming(null); }}
              />
            ) : (
              <>
                <Icon name="code" />
                {x.title}
              </>
            )}
            <span
              className="db-qtab-x"
              role="button"
              aria-label={t('Close tab')}
              onClick={(e) => { e.stopPropagation(); closeTab(x.id); }}
            >
              <Icon name="close" />
            </span>
          </div>
        ))}
        <IconButton icon="plus" label={t('New query tab')} size="sm" onClick={addTab} />
      </div>

      <div className="db-tb">
        <Button variant="primary" size="sm" icon="play" loading={run.running} onClick={runCurrent} title={t('Runs the selection, or the statement under the cursor')}>
          {t('Run')} <Kbd>Ctrl+↵</Kbd>
        </Button>
        <Button variant="secondary" size="sm" icon="play" onClick={runAll} title={t('Runs the whole script')}>
          {t('Run all')} <Kbd>Ctrl+⇧+↵</Kbd>
        </Button>
        <DropdownMenu
          aria-label={t('Templates')}
          items={templates.map((x) => ({ id: x.id, label: x.label, icon: 'code', onSelect: () => editor.current?.insert(x.sql) }))}
          trigger={(p) => <Button size="sm" variant="ghost" icon="code" {...(p as any)}>{t('Templates')}</Button>}
        />
        <Button size="sm" variant="ghost" icon="clock" onClick={() => setHistOpen(true)}>{t('History')}</Button>
        <span className="db-tb-sep" />
        {inst.engine !== 'sqlite' && (
          <div style={{ minWidth: 180 }}>
            <Select
              compact
              value={db}
              onChange={onDbChange}
              options={(dbs.data ?? []).map((d) => ({ value: d.name, label: d.name }))}
            />
          </div>
        )}
      </div>

      <SqlEditor
        value={tab.sql}
        onChange={updateSql}
        engine={inst.engine}
        schema={schema}
        defaultTable={table || undefined}
        onRun={runCurrent}
        onRunAll={runAll}
        apiRef={editor}
        placeholder={t('Write SQL… Ctrl+Enter runs the statement under the cursor, Ctrl+Space completes names')}
      />

      {run.error && <div className="db-qerr" role="alert">{run.error}</div>}
      {run.result && (
        <>
          <div className="db-qres-h">
            <span><b>{run.result.time_ms} ms</b></span>
            {run.result.columns.length > 0 ? (
              <span>{t('{n} rows', { n: run.result.rows.length })}{run.result.truncated ? ` · ${t('first 1000 shown')}` : ''}</span>
            ) : (
              <span>{t('{n} rows affected', { n: run.result.affected })}</span>
            )}
            {(run.result.statements ?? 1) > 1 && <span>{t('{n} statements', { n: run.result.statements ?? 1 })}</span>}
            {run.limited && <span className="db-muted">{t('LIMIT {n} added (Settings)', { n: run.limited })}</span>}
            <span className="db-tb-sep" />
            {run.result.rows.length > 0 && (
              <DropdownMenu
                aria-label={t('Export')}
                items={[
                  { id: 'csv', label: 'CSV', icon: 'download', onSelect: () => void exportResult('csv') },
                  { id: 'json', label: 'JSON', icon: 'download', onSelect: () => void exportResult('json') },
                  { id: 'sql', label: 'SQL INSERT', icon: 'download', onSelect: () => void exportResult('sql') },
                ]}
                trigger={(p) => <Button size="sm" variant="secondary" icon="download" {...(p as any)}>{t('Export')}</Button>}
              />
            )}
          </div>
          {run.result.columns.length > 0 && <ResultTable result={run.result} nullText={settings.nullText} />}
        </>
      )}

      <Dialog open={histOpen} onClose={() => setHistOpen(false)} title={t('Query history')} icon="clock" size="lg">
        <div className="db-form">
          <Input compact icon="search" autoFocus value={histQ} placeholder={t('Search history…')} onChange={(e) => setHistQ(e.target.value)} />
          <div className="db-hist">
            {hist.length === 0 && <div className="db-empty-pane">{t('No queries yet')}</div>}
            {hist.map((h, i) => (
              <button
                key={i}
                type="button"
                className="db-hist-it"
                title={h.sql}
                onClick={() => {
                  updateSql(h.sql);
                  setHistOpen(false);
                  setTimeout(() => editor.current?.focus(), 0);
                }}
              >
                <code>{h.sql.replace(/\s+/g, ' ')}</code>
                <span>{h.instance ? `${h.instance} · ` : ''}{timeAgo(h.at)}</span>
              </button>
            ))}
          </div>
        </div>
      </Dialog>
    </div>
  );
}

function ResultTable({ result, nullText }: { result: QueryResult; nullText: string }) {
  return (
    <div className="db-gridwrap" style={{ maxHeight: '55vh' }}>
      <table className="db-dg" style={{ tableLayout: 'auto', minWidth: '100%' }}>
        <thead><tr>{result.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>
          {result.rows.map((r, i) => (
            <tr key={i}>
              {result.columns.map((c, j) => (
                <td key={j} style={{ maxWidth: 420 }} title={cellText(r[c]).slice(0, 500)}>
                  {r[c] === null || r[c] === undefined ? <span className="db-null">{nullText || 'NULL'}</span> : cellText(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
