import { getSdk } from './sdk';
import { CONFIG_DIR } from './settings';
import { createCache, invalidate } from './store';

export interface DbInstance {
  id: string;
  name: string;
  type: 'mysql' | 'mariadb' | 'postgres' | 'sqlite';
  engine: 'mysql' | 'postgres' | 'sqlite';
  mode: 'docker' | 'native' | 'file';
  host?: string;
  port?: number;
  container?: string;
  /** Native service name (Windows service manager). */
  service?: string;
  path?: string;
  status: 'running' | 'stopped' | 'ready';
  version?: string;
  user?: string;
  password?: string;
}

export interface DatabaseInfo {
  name: string;
  charset?: string;
  collation?: string;
  size_bytes?: number;
  tables_count?: number;
}

export interface TableInfo {
  name: string;
  type: string;
  engine: string;
  rows_count: number;
  data_size: number;
  index_size: number;
  collation: string;
  comment: string;
}

export interface ColumnInfo {
  name: string;
  type: string;
  nullable: string;
  key_type: string;
  default_val: any;
  extra: string;
  comment: string;
}

export interface IndexInfo {
  name: string;
  column: string;
  unique: boolean;
  seq: number;
}

export interface QueryResult {
  columns: string[];
  rows: Record<string, any>[];
  affected: number;
  time_ms: number;
  total_rows?: number;
  truncated?: boolean;
  statements?: number;
}

/** Column details returned with the data so the grid knows the primary key without a second request. */
export interface ColumnMeta {
  name: string;
  type: string;
  pk: boolean;
  nullable: boolean;
  auto: boolean;
  default: any;
}

export type FilterOp = 'eq' | 'ne' | 'gt' | 'lt' | 'gte' | 'lte' | 'contains' | 'starts' | 'ends' | 'null' | 'notnull';
export interface ColFilter {
  col: string;
  op: FilterOp;
  value?: string;
}

export interface BrowseParams {
  database: string;
  table: string;
  page?: number;
  per_page?: number;
  sort_col?: string;
  sort_dir?: 'ASC' | 'DESC';
  /** Quick search across all text columns (bound LIKE). */
  search?: string;
  col_filters?: ColFilter[];
  /** Raw SQL WHERE clause (advanced). */
  filter?: string;
}

export interface BrowseDataResult {
  columns: string[];
  rows: Record<string, any>[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
  meta: ColumnMeta[];
  primary_keys: string[];
}

export interface DbUser {
  user: string;
  host: string;
  privileges?: string;
}

export interface LogStatus {
  status: {
    slow_query_log: string;
    long_query_time: string;
    log_file: string;
  };
  content: string;
}

export interface DetectResult {
  instances: DbInstance[];
  sqlite_files: DbInstance[];
  supported_versions: Record<string, string[]>;
}

function toBase64(text: string): string {
  return btoa(unescape(encodeURIComponent(text)));
}

function randomId(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

/** The command arg is capped at 4096 chars by the manifest. */
const ARG_LIMIT = 4000;
const TMP_DIR = `${CONFIG_DIR}/tmp`;
let fileTransport = true;
let tmpReady: Promise<void> | null = null;

/** Creates the payload folder once and removes payload files left over by requests that never reached the bridge. */
function ensureTmpDir(): Promise<void> {
  if (!tmpReady) {
    const files = getSdk().files;
    tmpReady = (async () => {
      try {
        const left = await files.list(TMP_DIR);
        await Promise.all(left.filter((f) => f.type === 'file').map((f) => files.remove(`${TMP_DIR}/${f.name}`).catch(() => undefined)));
      } catch {
        await files.mkdir(CONFIG_DIR).catch(() => undefined);
        await files.mkdir(TMP_DIR).catch(() => undefined);
      }
    })();
  }
  return tmpReady;
}

/**
 * Payloads that are too long for a command argument, or that carry a password, go through a private file
 * (~/.config/ervisio/plugins/database/tmp/<random>.json) that the bridge reads and deletes. Keeps passwords
 * out of the process list and lifts the size limit on queries and cell values.
 */
async function encodePayload(payload: unknown): Promise<string> {
  const json = JSON.stringify(payload);
  const b64 = toBase64(json);
  const secret = /"password":"[^"]/.test(json);
  if (fileTransport && (secret || b64.length > ARG_LIMIT)) {
    const name = randomId();
    try {
      await ensureTmpDir();
      await getSdk().files.write(`${CONFIG_DIR}/tmp/${name}.json`, json);
      return `@file:${name}`;
    } catch {
      // Older console or folder not writable: fall back to the argument when it fits.
      fileTransport = false;
    }
  }
  if (b64.length > ARG_LIMIT) throw new Error('Request too large for this console (more than 4 KB). Update Ervisio to 0.5 or later.');
  return b64;
}

export async function callBridge<T>(action: string, payload: any = {}): Promise<T> {
  const sdk = getSdk();
  const res = await sdk.api.exec('db-bridge', [action, await encodePayload(payload)]);

  if (res.exitCode !== 0 && !res.stdout.trim()) {
    throw new Error(res.stderr || `Bridge error (exit ${res.exitCode})`);
  }

  let data: any;
  try {
    data = JSON.parse(res.stdout);
  } catch {
    throw new Error(`Invalid response from database engine: ${res.stdout || res.stderr}`);
  }
  if (data && data.error) throw new Error(data.error.message || 'Database error');
  return data as T;
}

export const dbApi = {
  detect: () => callBridge<DetectResult>('detect'),
  installDocker: (p: { engine: string; version: string; name: string; password: string; port: number }) =>
    callBridge<{ ok: boolean; container_id: string }>('install-docker', p),
  installApt: (p: { engine: string; password?: string }) =>
    callBridge<{ ok: boolean; package: string }>('install-apt', p),
  createSqlite: (p: { path: string }) =>
    callBridge<{ ok: boolean; path: string }>('create-sqlite', p),
  manageInstance: (p: { op: 'start' | 'stop' | 'restart' | 'remove'; mode: string; target: string }) =>
    callBridge<{ ok: boolean }>('manage-instance', p),
  listDatabases: (instance: DbInstance) =>
    callBridge<{ databases: DatabaseInfo[] }>('list-databases', { instance }),
  createDatabase: (instance: DbInstance, p: { name: string; charset?: string; collation?: string }) =>
    callBridge<{ ok: boolean }>('create-database', { instance, ...p }),
  dropDatabase: (instance: DbInstance, name: string) =>
    callBridge<{ ok: boolean }>('drop-database', { instance, name }),
  listTables: (instance: DbInstance, database: string) =>
    callBridge<{ tables: TableInfo[] }>('list-tables', { instance, database }),
  tableStructure: (instance: DbInstance, database: string, table: string) =>
    callBridge<{ columns: ColumnInfo[]; indexes: IndexInfo[]; create_sql: string }>('table-structure', { instance, database, table }),
  browseData: (instance: DbInstance, p: BrowseParams) =>
    callBridge<BrowseDataResult>('browse-data', { instance, ...p }),
  saveRow: (instance: DbInstance, p: { database: string; table: string; data: Record<string, any>; pks?: Record<string, any>; is_new?: boolean }) =>
    callBridge<{ ok: boolean; affected: number }>('save-row', { instance, ...p }),
  deleteRows: (instance: DbInstance, p: { database: string; table: string; pks_list: Record<string, any>[] }) =>
    callBridge<{ ok: boolean; affected: number }>('delete-row', { instance, ...p }),
  query: (instance: DbInstance, p: { database?: string; sql: string }) =>
    callBridge<QueryResult>('query', { instance, ...p }),
  listUsers: (instance: DbInstance) =>
    callBridge<{ users: DbUser[] }>('list-users', { instance }),
  createUser: (instance: DbInstance, p: { user: string; host?: string; password?: string; grants?: string[]; database?: string }) =>
    callBridge<{ ok: boolean }>('create-user', { instance, ...p }),
  dropUser: (instance: DbInstance, user: string, host: string = '%') =>
    callBridge<{ ok: boolean }>('drop-user', { instance, user, host }),
  getLogs: (instance: DbInstance) =>
    callBridge<LogStatus>('get-logs', { instance }),
  toggleLog: (instance: DbInstance, p: { enable: boolean; threshold?: number }) =>
    callBridge<{ ok: boolean }>('toggle-log', { instance, ...p }),
};

/* ---------- cached reads (see store.ts) ---------- */

export const detectCache = createCache<[], DetectResult>('detect', () => 'all', () => dbApi.detect());
export const databasesCache = createCache<[DbInstance], DatabaseInfo[]>('dbs', (i) => i.id, async (i) => (await dbApi.listDatabases(i)).databases || []);
export const tablesCache = createCache<[DbInstance, string], TableInfo[]>('tables', (i, db) => `${i.id}|${db}`, async (i, db) => (await dbApi.listTables(i, db)).tables || []);
export const structureCache = createCache<[DbInstance, string, string], { columns: ColumnInfo[]; indexes: IndexInfo[]; create_sql: string }>(
  'struct', (i, db, tb) => `${i.id}|${db}|${tb}`, (i, db, tb) => dbApi.tableStructure(i, db, tb)
);
/** One entry per table + page/sort/filters, so paging back and forth or returning to a table is instant. */
export const browseCache = createCache<[DbInstance, BrowseParams], BrowseDataResult>(
  'browse', (i, p) => `${i.id}|${p.database}|${p.table}|${JSON.stringify(p)}`, (i, p) => dbApi.browseData(i, p)
);
export function invalidateTableData(inst: DbInstance, db: string, table: string): void {
  invalidate((k) => k.startsWith(`browse:${inst.id}|${db}|${table}|`));
}
export const usersCache =createCache<[DbInstance], DbUser[]>('users', (i) => i.id, async (i) => (await dbApi.listUsers(i)).users || []);
export const logsCache = createCache<[DbInstance], LogStatus>('logs', (i) => i.id, (i) => dbApi.getLogs(i));

/** Drops the cached data of one instance (or one of its databases). */
export function invalidateInstance(inst: DbInstance, what: 'dbs' | 'tables' | 'users' | 'all', db?: string): void {
  const id = inst.id;
  invalidate((k) => {
    if (what === 'all') return k.includes(`:${id}`);
    if (what === 'dbs') return k === `dbs:${id}`;
    if (what === 'users') return k === `users:${id}`;
    return db ? k === `tables:${id}|${db}` : k.startsWith(`tables:${id}|`);
  });
}

export const SYSTEM_DBS = ['information_schema', 'performance_schema', 'mysql', 'sys', 'postgres', 'template0', 'template1'];
export const isSystemDb = (name: string) => SYSTEM_DBS.includes(name.toLowerCase());
