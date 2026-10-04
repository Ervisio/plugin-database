import { getSdk } from './sdk';
import { IT } from './i18n.it';

/**
 * Strings are written in English right where they are used: the English text is the key.
 * Other languages map that English text to a translation (src/i18n.it.ts). Missing entries fall back to English.
 *
 *   t('Delete row')                     // "Delete row" / "Elimina riga"
 *   t('{n} rows', { n: 12 })            // placeholders in braces
 */
const DICTS: Record<string, Record<string, string>> = { it: IT };

let lang = 'en';

export function registerAllStrings(): void {
  try {
    const sdk = getSdk() as unknown as { lang?: () => string };
    lang = (sdk.lang?.() || 'en').slice(0, 2);
  } catch {
    lang = 'en';
  }
}

export function t(s: string, vars?: Record<string, string | number>): string {
  let out = DICTS[lang]?.[s] ?? s;
  if (vars) for (const [k, v] of Object.entries(vars)) out = out.split(`{${k}}`).join(String(v));
  return out;
}
