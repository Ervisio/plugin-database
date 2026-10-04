/**
 * Plugin settings and small state files as JSON in ~/.config/ervisio/plugins/database (manifest files.write).
 * The plugin frame has no localStorage, so this is the only place that survives a reload.
 * Missing or unreadable files give the defaults. Writes to one file are queued so they cannot overlap.
 *
 *   const [settings, update] = useFile('settings');   // hook, shared between components
 *   await setFile('history', { items: [...] });       // outside components
 */
import { useEffect, useSyncExternalStore } from 'react';
import { getSdk } from './sdk';

export const CONFIG_DIR = '~/.config/ervisio/plugins/database';

export interface Settings {
  /** Search the data grid while typing; when off, only on Enter. */
  liveSearch: boolean;
  /** Live search starts at this many characters. */
  liveSearchMin: number;
  /** Wait this long after the last key before searching (ms). */
  liveSearchDelay: number;
  /** Rows per page in the data grid. */
  perPage: number;
  /** Show information_schema, mysql, sys, performance_schema, postgres in the tree. */
  showSystemDbs: boolean;
  /** Add LIMIT n to SELECTs without one in the query editor; 0 = off. */
  autoLimit: number;
  /** Queries kept in the history. */
  historySize: number;
  /** Ask before deleting rows from the grid. */
  confirmDelete: boolean;
  /** How NULL looks in the grid. */
  nullText: string;
  /** Refetch cached lists that are older than this many seconds when a view opens again; 0 = only on Refresh. */
  staleAfter: number;
}

export interface HistoryItem {
  sql: string;
  at: number;
  instance?: string;
  ms?: number;
}

export interface QueryTabState {
  id: string;
  title: string;
  sql: string;
}

export interface FileMap {
  settings: Settings;
  history: { items: HistoryItem[] };
  recent: { ids: string[] };
  /** Open query tabs per instance id. */
  'query-tabs': { byInstance: Record<string, { tabs: QueryTabState[]; active: string }> };
}
export type FileName = keyof FileMap;

export const DEFAULT_SETTINGS: Settings = {
  liveSearch: true,
  liveSearchMin: 2,
  liveSearchDelay: 300,
  perPage: 100,
  showSystemDbs: true,
  autoLimit: 1000,
  historySize: 100,
  confirmDelete: true,
  nullText: 'NULL',
  staleAfter: 0,
};

export const DEFAULTS: { [K in FileName]: FileMap[K] } = {
  settings: DEFAULT_SETTINGS,
  history: { items: [] },
  recent: { ids: [] },
  'query-tabs': { byInstance: {} },
};

const path = (name: FileName) => `${CONFIG_DIR}/${name}.json`;
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));

export async function loadFile<K extends FileName>(name: K): Promise<FileMap[K]> {
  try {
    const text = await getSdk().files.read(path(name));
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return { ...clone(DEFAULTS[name]), ...parsed };
  } catch {
    /* missing or invalid: defaults */
  }
  return clone(DEFAULTS[name]);
}

const queues = new Map<string, Promise<unknown>>();

function saveFile<K extends FileName>(name: K, value: FileMap[K]): Promise<void> {
  const run = async () => {
    await getSdk().files.write(path(name), JSON.stringify(value, null, 2) + '\n');
  };
  const prev = queues.get(name) ?? Promise.resolve();
  const next = prev.then(run, run);
  queues.set(name, next.catch(() => undefined));
  return next;
}

/* ---------- shared cache: one copy per file ---------- */

const cache = new Map<string, unknown>();
const loading = new Map<string, Promise<unknown>>();
const listeners = new Map<string, Set<() => void>>();
const notify = (name: string) => listeners.get(name)?.forEach((f) => f());

function subscribeFile(name: FileName, fn: () => void): () => void {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name)!.add(fn);
  return () => {
    listeners.get(name)!.delete(fn);
  };
}

/** Loads a file into the shared cache (once) and returns it. For code outside components. */
export async function ensureFile<K extends FileName>(name: K): Promise<FileMap[K]> {
  if (cache.has(name)) return cache.get(name) as FileMap[K];
  if (!loading.has(name)) {
    loading.set(name, loadFile(name).then((v) => {
      if (!cache.has(name)) cache.set(name, v);
      loading.delete(name);
      notify(name);
    }));
  }
  await loading.get(name);
  return cache.get(name) as FileMap[K];
}

/** The cached value, or the defaults while it loads. */
export function peekFile<K extends FileName>(name: K): FileMap[K] {
  return (cache.get(name) as FileMap[K] | undefined) ?? DEFAULTS[name];
}

/** Replaces a file's value in the cache and saves it. Save errors are reported once in the console. */
export async function setFile<K extends FileName>(name: K, value: FileMap[K]): Promise<void> {
  cache.set(name, value);
  notify(name);
  try {
    await saveFile(name, value);
  } catch (e) {
    console.warn(`[database] could not save ${name}.json`, e);
  }
}

/** Current value (defaults until loaded), a function that merges a patch and saves, and whether it has loaded. */
export function useFile<K extends FileName>(name: K): [FileMap[K], (patch: Partial<FileMap[K]>) => Promise<void>, boolean] {
  const value = useSyncExternalStore(
    (f) => subscribeFile(name, f),
    () => cache.get(name) as FileMap[K] | undefined
  );
  useEffect(() => {
    void ensureFile(name);
  }, [name]);
  const update = (patch: Partial<FileMap[K]>) => setFile(name, { ...peekFile(name), ...patch } as FileMap[K]);
  return [value ?? DEFAULTS[name], update, value !== undefined];
}

export const useSettings = () => useFile('settings');
export const getSettings = (): Settings => peekFile('settings');

/** Adds a query to the history file (newest first, no duplicates, trimmed to the history size). */
export async function pushHistory(item: HistoryItem): Promise<void> {
  const h = await ensureFile('history');
  const size = getSettings().historySize || 100;
  const items = [item, ...h.items.filter((x) => x.sql !== item.sql)].slice(0, size);
  await setFile('history', { items });
}

export async function pushRecent(id: string): Promise<void> {
  const r = await ensureFile('recent');
  await setFile('recent', { ids: [id, ...r.ids.filter((x) => x !== id)].slice(0, 8) });
}
