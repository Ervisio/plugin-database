/**
 * Keyed cache for data fetched through the bridge.
 *
 * Going back to a view shows the cached data at once and does NOT fetch again: data is refetched only when
 * the user presses Refresh, after a change that invalidates it, or (optional setting) when it is older than
 * `staleAfter` seconds. Components that use the same key share one request.
 *
 *   const tables = createCache('tables', (i, db) => `${i.id}|${db}`, (i, db) => dbApi.listTables(i, db));
 *   const { data, loading, error, refresh } = tables.use(inst && db ? [inst, db] : null);
 *   invalidate((key) => key.startsWith(`tables:${inst.id}|`));
 */
import { useEffect, useSyncExternalStore } from 'react';
import { getSettings } from './settings';

export interface CacheState<T> {
  data: T | undefined;
  error: string | null;
  /** True while a request runs. `data` may still hold the previous answer. */
  loading: boolean;
  /** When the data arrived (ms), 0 if never. */
  at: number;
}

interface Entry<T> {
  state: CacheState<T>;
  promise?: Promise<T>;
  subs: Set<() => void>;
  run(): Promise<T>;
}

const IDLE: CacheState<never> = { data: undefined, error: null, loading: false, at: 0 };
const registry = new Map<string, Entry<unknown>>();

function set<T>(e: Entry<T>, patch: Partial<CacheState<T>>) {
  e.state = { ...e.state, ...patch };
  e.subs.forEach((f) => f());
}

function load<T>(e: Entry<T>): Promise<T> {
  if (e.promise) return e.promise;
  set(e, { loading: true });
  const p = e.run().then(
    (data) => {
      e.promise = undefined;
      set(e, { data, error: null, loading: false, at: Date.now() });
      return data;
    },
    (err: unknown) => {
      e.promise = undefined;
      set(e, { error: err instanceof Error ? err.message : String(err), loading: false });
      throw err;
    }
  );
  e.promise = p;
  return p;
}

export interface Cache<A extends unknown[], T> {
  /** Hook. Pass null to skip (for example while no table is selected). */
  use(args: A | null): CacheState<T> & { refresh(): Promise<T | undefined> };
  /** Fetch (or join the request in flight) and return the data. Uses the cache unless `force`. */
  get(args: A, force?: boolean): Promise<T>;
  peek(args: A): T | undefined;
  /** Replace the cached data (optimistic updates). */
  mutate(args: A, fn: (cur: T | undefined) => T | undefined): void;
  key(args: A): string;
}

export function createCache<A extends unknown[], T>(name: string, keyOf: (...a: A) => string, fetcher: (...a: A) => Promise<T>): Cache<A, T> {
  const key = (args: A) => `${name}:${keyOf(...args)}`;
  const entry = (args: A): Entry<T> => {
    const k = key(args);
    let e = registry.get(k) as Entry<T> | undefined;
    if (!e) {
      e = { state: IDLE as CacheState<T>, subs: new Set(), run: () => fetcher(...args) };
      registry.set(k, e as Entry<unknown>);
    }
    return e;
  };

  return {
    key,
    use(args) {
      const e = args ? entry(args) : null;
      const state = useSyncExternalStore(
        (f) => {
          if (!e) return () => undefined;
          e.subs.add(f);
          return () => {
            e.subs.delete(f);
          };
        },
        () => (e ? e.state : (IDLE as CacheState<T>))
      );
      useEffect(() => {
        if (!e) return;
        const stale = getSettings().staleAfter;
        const old = stale > 0 && e.state.at > 0 && Date.now() - e.state.at > stale * 1000;
        if ((e.state.at === 0 && !e.state.error) || old) load(e).catch(() => undefined);
      }, [e]);
      return { ...state, refresh: () => (e ? load(e).catch(() => undefined) : Promise.resolve(undefined)) };
    },
    get(args, force) {
      const e = entry(args);
      if (!force && e.state.at > 0 && e.state.data !== undefined) return Promise.resolve(e.state.data);
      return load(e);
    },
    peek(args) {
      return (registry.get(key(args)) as Entry<T> | undefined)?.state.data;
    },
    mutate(args, fn) {
      const e = entry(args);
      set(e, { data: fn(e.state.data) });
    },
  };
}

/**
 * Marks cached entries as outdated. Entries on screen fetch again right away (keeping the old data meanwhile);
 * the others are dropped and fetch when they are next used.
 */
export function invalidate(match: (key: string) => boolean): void {
  for (const [k, e] of registry) {
    if (!match(k)) continue;
    if (e.subs.size > 0) load(e).catch(() => undefined);
    else registry.delete(k);
  }
}
