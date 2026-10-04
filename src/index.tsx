import { createElement, useEffect } from 'react';
import { setReact } from '@ervisio/plugin-sdk/react';
import { setSdk, type PluginSDK } from './sdk';
import { registerAllStrings, t } from './i18n';
import { injectStyles } from './styles/index';
import { registerDatabaseIcons, DB_NAV_ICONS } from './ui/icons';
import { getInstance, navigate, sectionOf, setSearch, useRoute, useSearch, type NavId, type Route } from './router';
import { detectCache } from './api';
import { ensureFile } from './settings';
import { InstancesView } from './views/InstancesView';
import { ExplorerView } from './views/explorer/ExplorerView';
import { ExplorerNav } from './views/explorer/ExplorerNav';
import { ActivityView } from './views/ActivityView';
import { SettingsView } from './views/SettingsView';
import { EmptyState, Icon, IconButton, Input } from './kit';

const GROUPS: { label: string; items: NavId[] }[] = [
  { label: 'Manage', items: ['engines'] },
  { label: 'Tools', items: ['activity', 'settings'] },
];
const NAV_LABEL: Record<NavId, string> = { engines: 'Engines', activity: 'Activity', settings: 'Settings' };

function TooOld() {
  return createElement(EmptyState, { icon: 'alert', title: 'Ervisio too old', text: 'This plugin needs Ervisio 0.5 or later.' });
}

function MainNav({ section }: { section: NavId }) {
  const det = detectCache.use([]);
  const engines = det.data ? det.data.instances.length + det.data.sqlite_files.length : undefined;
  const running = det.data ? [...det.data.instances, ...det.data.sqlite_files].filter((i) => i.status !== 'stopped').length : 0;
  const counts: Partial<Record<NavId, number>> = { engines };
  return (
    <nav className="db-nav" aria-label={t('Database navigation')}>
      {GROUPS.map((g) => (
        <div key={g.label} style={{ display: 'contents' }}>
          <div className="db-nav-gl">{t(g.label)}</div>
          {g.items.map((id) => (
            <button
              key={id}
              type="button"
              className="db-nav-it"
              aria-current={section === id ? 'page' : undefined}
              onClick={() => navigate({ view: id } as Route, { root: true })}
            >
              <Icon name={DB_NAV_ICONS[id]} />
              {t(NAV_LABEL[id])}
              {counts[id] !== undefined && <b>{counts[id]}</b>}
            </button>
          ))}
        </div>
      ))}
      {det.data && (
        <div className="db-nav-ft">
          <span className={`db-dot ${running ? 'db-dot--ok' : 'db-dot--warn'}`} />
          {t('{running} of {total} running', { running, total: engines ?? 0 })}
        </div>
      )}
    </nav>
  );
}

function ViewHost({ route, search }: { route: Route; search: string }) {
  if (route.view === 'explorer') {
    const inst = getInstance(route.instanceId);
    if (!inst) return <MissingInstance />;
    return <ExplorerView inst={inst} />;
  }
  if (route.view === 'activity') return <ActivityView />;
  if (route.view === 'settings') return <SettingsView search={search} />;
  return <InstancesView search={search} />;
}

function MissingInstance() {
  useEffect(() => {
    navigate({ view: 'engines' }, { root: true });
  }, []);
  return null;
}

const PLACEHOLDER: Record<Route['view'], string> = {
  engines: 'Search engines…',
  explorer: 'Filter databases and tables…',
  activity: 'Search engines…',
  settings: 'Search settings…',
};

function Shell() {
  const route = useRoute();
  const q = useSearch();
  const inst = route.view === 'explorer' ? getInstance(route.instanceId) : undefined;

  return (
    <div className="db-root hue-plg">
      <div className="db-shell">
        {inst ? <ExplorerNav inst={inst} filter={q} /> : <MainNav section={sectionOf(route)} />}
        <div className="db-main">
          {route.view !== 'activity' && (
            <div className="db-top">
              <Input
                fieldClassName="db-search"
                icon="search"
                value={q}
                placeholder={t(PLACEHOLDER[route.view])}
                aria-label={t(PLACEHOLDER[route.view])}
                onChange={(e) => setSearch(e.target.value)}
                end={q ? <IconButton icon="close" label={t('Clear')} size="sm" onClick={() => setSearch('')} /> : undefined}
              />
            </div>
          )}
          <div className="db-view">
            <ViewHost key={route.view === 'explorer' ? `x:${route.instanceId}` : route.view} route={route} search={q} />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function activate(sdk: PluginSDK): void {
  setSdk(sdk);
  setReact(sdk.react);
  registerAllStrings();
  injectStyles();
  registerDatabaseIcons();

  // Warm up: settings first (they shape the first render), engines in the background.
  void ensureFile('settings');
  void detectCache.get([]).catch(() => undefined);

  const ok = sdk.version >= 3;
  sdk.registerPage('database', ok ? Shell : TooOld);
}
