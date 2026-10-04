import type { ReactNode } from 'react';
import { DEFAULT_SETTINGS, setFile, useSettings, type Settings } from '../settings';
import { t } from '../i18n';
import { Button, Select, Switch, toast } from '../kit';
import { PageHeader } from '../ui/PageHeader';

interface Item {
  key: keyof Settings;
  title: string;
  text: string;
  group: string;
  control(v: Settings, set: (p: Partial<Settings>) => void): ReactNode;
}

const ITEMS: Item[] = [
  {
    key: 'liveSearch', group: 'Search', title: 'Search while typing',
    text: 'The data grid searches as you type. Turn off to search only when you press Enter.',
    control: (v, set) => <Switch checked={v.liveSearch} onChange={(c) => set({ liveSearch: c })} aria-label={t('Search while typing')} />,
  },
  {
    key: 'liveSearchMin', group: 'Search', title: 'Start from',
    text: 'Characters needed before a live search runs.',
    control: (v, set) => (
      <Select compact value={String(v.liveSearchMin)} onChange={(x) => set({ liveSearchMin: Number(x) })}
        options={[1, 2, 3, 4].map((n) => ({ value: String(n), label: t(n === 1 ? '{n} character' : '{n} characters', { n }) }))} />
    ),
  },
  {
    key: 'liveSearchDelay', group: 'Search', title: 'Typing pause',
    text: 'How long to wait after the last key before searching.',
    control: (v, set) => (
      <Select compact value={String(v.liveSearchDelay)} onChange={(x) => set({ liveSearchDelay: Number(x) })}
        options={[150, 300, 500, 800].map((n) => ({ value: String(n), label: `${n} ms` }))} />
    ),
  },
  {
    key: 'perPage', group: 'Data grid', title: 'Rows per page',
    text: 'Default page size when a table opens.',
    control: (v, set) => (
      <Select compact value={String(v.perPage)} onChange={(x) => set({ perPage: Number(x) })}
        options={[25, 50, 100, 250, 500, 1000].map((n) => ({ value: String(n), label: String(n) }))} />
    ),
  },
  {
    key: 'confirmDelete', group: 'Data grid', title: 'Confirm before deleting rows',
    text: 'Ask before deleting rows from the grid.',
    control: (v, set) => <Switch checked={v.confirmDelete} onChange={(c) => set({ confirmDelete: c })} aria-label={t('Confirm before deleting rows')} />,
  },
  {
    key: 'nullText', group: 'Data grid', title: 'NULL looks like',
    text: 'Text shown in cells that are NULL.',
    control: (v, set) => (
      <Select compact value={v.nullText} onChange={(x) => set({ nullText: x })}
        options={['NULL', '(null)', '∅', '—'].map((s) => ({ value: s, label: s }))} />
    ),
  },
  {
    key: 'showSystemDbs', group: 'Explorer', title: 'Show system databases',
    text: 'information_schema, mysql, sys, performance_schema, postgres.',
    control: (v, set) => <Switch checked={v.showSystemDbs} onChange={(c) => set({ showSystemDbs: c })} aria-label={t('Show system databases')} />,
  },
  {
    key: 'staleAfter', group: 'Explorer', title: 'Refresh lists when reopened',
    text: 'Lists are cached so going back is instant. Refresh them when they are older than this.',
    control: (v, set) => (
      <Select compact value={String(v.staleAfter)} onChange={(x) => set({ staleAfter: Number(x) })}
        options={[
          { value: '0', label: t('Only on Refresh') },
          { value: '60', label: t('After 1 minute') },
          { value: '300', label: t('After 5 minutes') },
          { value: '1800', label: t('After 30 minutes') },
        ]} />
    ),
  },
  {
    key: 'autoLimit', group: 'Query editor', title: 'Automatic LIMIT',
    text: 'Add LIMIT to a single SELECT that has none, so a big table cannot flood the page.',
    control: (v, set) => (
      <Select compact value={String(v.autoLimit)} onChange={(x) => set({ autoLimit: Number(x) })}
        options={[{ value: '0', label: t('Off') }, ...[100, 500, 1000, 5000].map((n) => ({ value: String(n), label: `LIMIT ${n}` }))]} />
    ),
  },
  {
    key: 'historySize', group: 'Query editor', title: 'History size',
    text: 'Queries kept in the history.',
    control: (v, set) => (
      <Select compact value={String(v.historySize)} onChange={(x) => set({ historySize: Number(x) })}
        options={[50, 100, 250, 500].map((n) => ({ value: String(n), label: String(n) }))} />
    ),
  },
];

/** One searchable page; every change saves at once with an Undo toast (DESIGN-RULES). */
export function SettingsView({ search }: { search: string }) {
  const [settings, update, loaded] = useSettings();
  const q = search.trim().toLowerCase();

  const set = (p: Partial<Settings>) => {
    const before = { ...settings };
    void update(p).then(() => toast.undo(t('Setting saved'), t('Undo'), () => void setFile('settings', before)));
  };

  const shown = ITEMS.filter((i) => !q || `${t(i.title)} ${t(i.text)} ${t(i.group)}`.toLowerCase().includes(q));
  const groups = [...new Set(shown.map((i) => i.group))];

  return (
    <div className="db-col" style={{ gap: 16 }}>
      <PageHeader
        icon="cog"
        title={t('Settings')}
        subtitle={t('Saved on the server for your user')}
        actions={<Button variant="ghost" size="sm" icon="undo" disabled={!loaded} onClick={() => set({ ...DEFAULT_SETTINGS })}>{t('Reset to defaults')}</Button>}
      />
      {shown.length === 0 && <div className="db-empty-pane">{t('No setting matches “{q}”', { q: search })}</div>}
      {groups.map((g) => (
        <section key={g} className="db-col" style={{ gap: 6 }}>
          <h2 className="db-section-t">{t(g)}</h2>
          <div className="db-set">
            {shown.filter((i) => i.group === g).map((i) => (
              <div key={i.key} className="db-set-it">
                <div className="db-set-tx">
                  <b>{t(i.title)}</b>
                  <span>{t(i.text)}</span>
                </div>
                <div className="db-set-ctl">{i.control(settings, set)}</div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
