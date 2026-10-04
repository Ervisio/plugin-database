/**
 * UI state of the explorer, per instance and per table. Lives at module level so leaving an engine and coming
 * back (or switching tables) restores the open database, table, tab, page, sort and filters.
 */
import { useSyncExternalStore } from 'react';
import type { ColFilter } from './api';

export type ExplorerTab = 'data' | 'structure' | 'query' | 'users' | 'logs';

export interface GridState {
  page: number;
  perPage: number;
  sortCol: string;
  sortDir: 'ASC' | 'DESC';
  /** What is typed in the quick search box. */
  searchInput: string;
  /** The search actually applied (after debounce or Enter). */
  search: string;
  colFilters: ColFilter[];
  showColFilters: boolean;
  /** Raw WHERE: typed text and applied text. */
  whereInput: string;
  where: string;
  showWhere: boolean;
  widths: Record<string, number>;
}

export interface InstanceState {
  db: string;
  table: string;
  tab: ExplorerTab;
  /** Expanded database nodes in the tree. */
  expanded: string[];
  grids: Record<string, GridState>;
}

const states = new Map<string, InstanceState>();
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

const EMPTY: InstanceState = { db: '', table: '', tab: 'data', expanded: [], grids: {} };

export function newGrid(perPage: number): GridState {
  return {
    page: 1, perPage, sortCol: '', sortDir: 'ASC',
    searchInput: '', search: '', colFilters: [], showColFilters: false,
    whereInput: '', where: '', showWhere: false, widths: {},
  };
}

export function getExplorer(id: string): InstanceState {
  return states.get(id) ?? EMPTY;
}

export function patchExplorer(id: string, patch: Partial<InstanceState> | ((s: InstanceState) => Partial<InstanceState>)): void {
  const cur = getExplorer(id);
  const p = typeof patch === 'function' ? patch(cur) : patch;
  states.set(id, { ...cur, ...p });
  emit();
}

export function useExplorer(id: string): InstanceState {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => {
        subs.delete(f);
      };
    },
    () => getExplorer(id)
  );
}

export const gridKey = (db: string, table: string) => `${db}.${table}`;

export function patchGrid(id: string, key: string, perPage: number, patch: Partial<GridState>): void {
  patchExplorer(id, (s) => ({ grids: { ...s.grids, [key]: { ...(s.grids[key] ?? newGrid(perPage)), ...patch } } }));
}

/** Opens a table: selects its database too and expands it in the tree. */
export function openTable(id: string, db: string, table: string, tab?: ExplorerTab): void {
  patchExplorer(id, (s) => ({
    db,
    table,
    tab: tab ?? (s.tab === 'users' || s.tab === 'logs' ? 'data' : s.tab),
    expanded: s.expanded.includes(db) ? s.expanded : [...s.expanded, db],
  }));
}

export function toggleExpanded(id: string, db: string, open?: boolean): void {
  patchExplorer(id, (s) => {
    const isOpen = s.expanded.includes(db);
    const want = open ?? !isOpen;
    if (want === isOpen) return {};
    return { expanded: want ? [...s.expanded, db] : s.expanded.filter((x) => x !== db) };
  });
}
