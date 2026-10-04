import { useEffect, useRef } from 'react';
import { EditorView, keymap, placeholder as cmPlaceholder } from '@codemirror/view';
import { Compartment, EditorState, Prec } from '@codemirror/state';
import { basicSetup } from 'codemirror';
import { sql, MySQL, PostgreSQL, SQLite, type SQLNamespace } from '@codemirror/lang-sql';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { tags } from '@lezer/highlight';

/** Colours from the host theme, so the editor follows light and dark themes. */
const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword, tags.modifier], color: 'var(--acc)', fontWeight: '600' },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--ok)' },
  { tag: [tags.number, tags.bool, tags.null], color: 'var(--warn)' },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: 'var(--ink3)', fontStyle: 'italic' },
  { tag: [tags.typeName, tags.standard(tags.name)], color: 'var(--info)' },
  { tag: [tags.special(tags.name), tags.quote], color: 'var(--h-usr, var(--info))' },
  { tag: [tags.operator, tags.punctuation, tags.bracket], color: 'var(--ink2)' },
]);

export interface SqlEditorApi {
  /** The selection, or else the statement under the cursor. */
  current(): string;
  all(): string;
  focus(): void;
  insert(text: string): void;
}

/** Splits a script into statements with their positions (same rules as the bridge: ; outside quotes/comments). */
export function statementRanges(text: string): { from: number; to: number; sql: string }[] {
  const out: { from: number; to: number; sql: string }[] = [];
  let start = 0;
  let quote: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const nx = text[i + 1];
    if (quote) {
      if (ch === '\\' && quote !== '`') i++;
      else if (ch === quote) quote = null;
    } else if (ch === "'" || ch === '"' || ch === '`') quote = ch;
    else if (ch === '-' && nx === '-') {
      const j = text.indexOf('\n', i);
      i = j < 0 ? text.length : j;
    } else if (ch === '/' && nx === '*') {
      const j = text.indexOf('*/', i + 2);
      i = j < 0 ? text.length : j + 1;
    } else if (ch === ';') {
      out.push({ from: start, to: i + 1, sql: text.slice(start, i).trim() });
      start = i + 1;
    }
  }
  if (text.slice(start).trim()) out.push({ from: start, to: text.length, sql: text.slice(start).trim() });
  return out.filter((s) => s.sql && !/^(--[^\n]*\n?|\/\*[\s\S]*?\*\/|\s)*$/.test(s.sql));
}

export function SqlEditor({ value, onChange, engine, schema, defaultTable, onRun, onRunAll, apiRef, placeholder }: {
  value: string;
  onChange(v: string): void;
  engine: string;
  schema: SQLNamespace;
  defaultTable?: string;
  onRun(): void;
  onRunAll(): void;
  apiRef?: { current: SqlEditorApi | null };
  placeholder?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const lang = useRef(new Compartment());
  const cb = useRef({ onChange, onRun, onRunAll });
  cb.current = { onChange, onRun, onRunAll };

  const langExt = () => sql({
    dialect: engine === 'postgres' ? PostgreSQL : engine === 'sqlite' ? SQLite : MySQL,
    schema,
    defaultTable,
    upperCaseKeywords: true,
  });

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          Prec.highest(keymap.of([
            { key: 'Mod-Enter', run: () => { cb.current.onRun(); return true; } },
            { key: 'Mod-Shift-Enter', run: () => { cb.current.onRunAll(); return true; } },
            { key: 'F9', run: () => { cb.current.onRunAll(); return true; } },
          ])),
          basicSetup,
          lang.current.of(langExt()),
          syntaxHighlighting(highlight),
          cmPlaceholder(placeholder ?? ''),
          EditorView.lineWrapping,
          EditorView.updateListener.of((u) => {
            if (u.docChanged) cb.current.onChange(u.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = v;
    if (apiRef) {
      apiRef.current = {
        all: () => v.state.doc.toString(),
        current: () => {
          const sel = v.state.selection.main;
          const text = v.state.doc.toString();
          if (!sel.empty) return text.slice(sel.from, sel.to);
          const ranges = statementRanges(text);
          const hit = ranges.find((r) => sel.head >= r.from && sel.head <= r.to)
            ?? [...ranges].reverse().find((r) => r.from <= sel.head)
            ?? ranges[0];
          return hit?.sql ?? '';
        },
        focus: () => v.focus(),
        insert: (s: string) => {
          v.dispatch(v.state.replaceSelection(s));
          v.focus();
        },
      };
    }
    return () => {
      v.destroy();
      view.current = null;
      if (apiRef) apiRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Switching query tabs replaces the text.
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  // New schema (other database, tables loaded) or engine.
  useEffect(() => {
    view.current?.dispatch({ effects: lang.current.reconfigure(langExt()) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, schema, defaultTable]);

  return <div className="db-sql" ref={host} />;
}
