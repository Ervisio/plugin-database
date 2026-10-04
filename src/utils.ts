export function buildConnectionString(inst: {
  type: string;
  host?: string;
  port?: number;
  user?: string;
  password?: string;
  path?: string;
}): string {
  if (inst.type === 'sqlite') return `sqlite:///${inst.path || ''}`;
  const scheme = inst.type === 'postgres' ? 'postgresql' : 'mysql';
  const user = inst.user || (inst.type === 'postgres' ? 'postgres' : 'root');
  const pass = inst.password ? `:${encodeURIComponent(inst.password)}` : '';
  const host = inst.host || '127.0.0.1';
  const port = inst.port ? `:${inst.port}` : '';
  return `${scheme}://${user}${pass}@${host}${port}/`;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Sandboxed frame may deny clipboard: fall back to a temp textarea + execCommand
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

/* ---------- export formats ---------- */

type Row = Record<string, any>;

export function toCsv(cols: string[], rows: Row[]): string {
  const cell = (v: any) => {
    if (v === null || v === undefined) return '';
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n\r;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.map(cell).join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n') + '\n';
}

export function toJson(cols: string[], rows: Row[]): string {
  return JSON.stringify(rows.map((r) => Object.fromEntries(cols.map((c) => [c, r[c] ?? null]))), null, 2) + '\n';
}

export function quoteIdent(engine: string, name: string): string {
  return engine === 'mysql' ? `\`${name.replace(/`/g, '``')}\`` : `"${name.replace(/"/g, '""')}"`;
}

/** Backslash is an escape character only in MySQL strings. */
export function sqlLiteral(v: any, engine = 'mysql'): string {
  if (v === null || v === undefined) return 'NULL';
  if (typeof v === 'number' || typeof v === 'bigint') return String(v);
  if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE';
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
  const esc = engine === 'mysql' ? s.replace(/\\/g, '\\\\') : s;
  return `'${esc.replace(/'/g, "''")}'`;
}

export function toInsert(engine: string, table: string, cols: string[], rows: Row[]): string {
  const head = `INSERT INTO ${quoteIdent(engine, table)} (${cols.map((c) => quoteIdent(engine, c)).join(', ')}) VALUES`;
  return rows.map((r) => `${head} (${cols.map((c) => sqlLiteral(r[c], engine)).join(', ')});`).join('\n') + '\n';
}

export function cellText(v: any): string {
  if (v === null || v === undefined) return '';
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

export function timeAgo(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}
