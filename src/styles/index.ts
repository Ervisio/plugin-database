const files = import.meta.glob<string>('./*.css', { query: '?inline', import: 'default', eager: true });

export function injectStyles(): void {
  if (document.getElementById('db-styles')) return;
  const el = document.createElement('style');
  el.id = 'db-styles';
  el.textContent = Object.keys(files).sort().map((k) => files[k]).join('\n');
  document.head.appendChild(el);
}
