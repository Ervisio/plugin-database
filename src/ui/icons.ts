import { getSdk } from '../sdk';

/**
 * Icons the host kit does not have. Registered once in activate() via sdk.ui.registerIcon (same as plugin-docker).
 * 24x24 stroke paths so they blend with the host set.
 */
export function registerDatabaseIcons(): void {
  const reg = getSdk().ui.registerIcon as ((name: string, svg: string) => void) | undefined;
  if (!reg) return;
  reg('db-database', '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13"/><path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>');
  reg('db-table', '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9.5h18M3 14.5h18M9.5 9.5V20"/>');
  reg('db-view', '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9.5h18"/><circle cx="12" cy="15" r="2.2"/>');
  reg('db-open', '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/>');
  reg('db-mysql', '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>');
  reg('db-postgres', '<path d="M12 3c-4 0-7 2.5-7 6 0 2.5 1.2 4.4 3 5.5V18l2 1.5L12 21l2-1.5 2-1.5v-3.5c1.8-1.1 3-3 3-5.5 0-3.5-3-6-7-6z"/><circle cx="10" cy="8.5" r="1"/>');
  reg('db-sqlite', '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/>');
  reg('db-docker', '<rect x="3" y="10" width="18" height="9" rx="2"/><path d="M7 10V6h4v4M11 10V6h4v4"/>');
  reg('db-key', '<path d="M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L19 3m-4 4l3 3"/>');
  reg('db-chevright', '<path d="M9 6l6 6-6 6"/>');
}

/** Sidebar icon per entry. */
export const DB_NAV_ICONS = {
  engines: 'db-database',
  activity: 'clock',
  settings: 'cog',
} as const;

/** Engine mark per db type. */
export function engineIcon(type: string): string {
  if (type === 'postgres') return 'db-postgres';
  if (type === 'sqlite') return 'db-sqlite';
  return 'db-mysql';
}
