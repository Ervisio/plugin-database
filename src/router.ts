import { useSyncExternalStore } from 'react';
import type { DbInstance } from './api';

export type Route =
  | { view: 'engines' }
  | { view: 'explorer'; instanceId: string }
  | { view: 'activity' }
  | { view: 'settings' };

export type View = Route['view'];
/** Sidebar entries (the explorer is reached from Engines). */
export type NavId = 'engines' | 'activity' | 'settings';

const HOME: Route = { view: 'engines' };
let history: Route[] = [HOME];
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
const same = (a: Route, b: Route) => JSON.stringify(a) === JSON.stringify(b);
/** The top search filters the current view, so it starts empty on another view. */
const changed = (prev: Route) => {
  if (prev.view !== currentRoute().view && query) setSearch('');
  emit();
};

export function navigate(to: Route, opts: { root?: boolean; replace?: boolean } = {}): void {
  const cur = history[history.length - 1];
  if (opts.root) history = [to];
  else if (opts.replace) history = [...history.slice(0, -1), to];
  else if (!same(cur, to)) history = [...history, to];
  else return;
  changed(cur);
}

export function back(): void {
  const cur = currentRoute();
  history = history.length > 1 ? history.slice(0, -1) : [HOME];
  changed(cur);
}

export const currentRoute = (): Route => history[history.length - 1];
export const canGoBack = (): boolean => history.length > 1;

const subscribe = (f: () => void) => {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
};

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, currentRoute);
}

export function useCanGoBack(): boolean {
  return useSyncExternalStore(subscribe, canGoBack);
}

export function sectionOf(r: Route): NavId {
  return r.view === 'explorer' ? 'engines' : r.view;
}

/* ---------- opened instances (survive navigation; manual connections are not in detect) ---------- */

const instances = new Map<string, DbInstance>();

export function getInstance(id: string): DbInstance | undefined {
  return instances.get(id);
}

export function openExplorer(inst: DbInstance): void {
  instances.set(inst.id, inst);
  navigate({ view: 'explorer', instanceId: inst.id });
}

/* ---------- global search (top bar) ---------- */

let query = '';
const qsubs = new Set<() => void>();

export function setSearch(q: string): void {
  query = q;
  qsubs.forEach((f) => f());
}

export function useSearch(): string {
  return useSyncExternalStore(
    (f) => {
      qsubs.add(f);
      return () => {
        qsubs.delete(f);
      };
    },
    () => query
  );
}
