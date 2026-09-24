'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, Layers } from 'lucide-react';
import { cn } from '@/lib/utils';
import { scopeLabel } from '@/lib/chat-types';
import type { MappedError } from '@/lib/errors';
import { ErrorBanner } from '@/components/chat/error-banner';
import type { Collection } from '@/types/weave-api';

interface ScopePickerProps {
  collections: Collection[] | null;
  collectionsError?: MappedError | null;
  /** Slugs currently selected as this turn's collection FILTER — empty
   * means no filter (search everything the bot/team combination already
   * allows). See ChatRequestBody.collections's own docstring in
   * types/weave-api.ts. */
  selectedCollections: string[];
  onToggleCollection: (slug: string) => void;
  onClearCollections: () => void;
}

/**
 * The composer's "Wissensbereiche" pill button — opens a popover with one
 * checkbox per collection the caller may read, plus an "Alle meine
 * Bereiche" option that stands for the empty selection (see
 * `scopeLabel`/`ChatRequestBody.collections`'s own docstrings for why empty
 * means "no filter", not "filter to nothing"). Replaces the old sidebar's
 * always-visible collection tag list — see chat-app.tsx.
 */
export function ScopePicker({
  collections,
  collectionsError,
  selectedCollections,
  onToggleCollection,
  onClearCollections,
}: ScopePickerProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  // Close on outside click and on Escape, returning focus to the button —
  // the same pattern this app already needs for any popover, kept local to
  // this component since nothing else in the codebase has one yet.
  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
      }
    }

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const label = scopeLabel(selectedCollections, collections);
  const allSelected = selectedCollections.length === 0;

  return (
    <div className="relative" ref={containerRef}>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-controls={menuId}
        className={cn(
          'inline-flex h-9 min-h-[40px] items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors sm:min-h-0',
          open
            ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-ink,var(--accent))]'
            : 'border-[var(--border)] bg-[var(--surface-muted)] text-[var(--foreground-muted)] hover:bg-[var(--surface)]'
        )}
      >
        <Layers className="h-3.5 w-3.5 text-[var(--accent)]" aria-hidden="true" />
        <span className="sr-only">Wissensbereiche: </span>
        <span className="max-w-[10rem] truncate">{label}</span>
        <ChevronDown className="h-3 w-3" aria-hidden="true" />
      </button>

      {open ? (
        <div
          id={menuId}
          role="group"
          aria-label="In welchen Wissensbereichen suchen?"
          className="absolute bottom-full left-0 z-30 mb-2 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-[var(--border)] bg-[var(--surface)] p-1.5 shadow-lg"
        >
          <p className="px-2 py-1.5 text-[11px] font-semibold text-[var(--foreground-muted)]">
            In welchen Wissensbereichen suchen?
          </p>

          <label className="flex min-h-[40px] cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm font-medium hover:bg-[var(--surface-muted)]">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => onClearCollections()}
              className="h-4 w-4 flex-shrink-0 accent-[var(--accent)]"
            />
            Alle meine Bereiche
          </label>

          {collectionsError ? (
            <div className="px-2 py-1.5">
              <ErrorBanner error={collectionsError} />
            </div>
          ) : collections === null ? (
            <p className="px-2 py-1.5 text-xs text-[var(--foreground-muted)]">Wissensbereiche werden geladen…</p>
          ) : collections.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-[var(--foreground-muted)]">Für dich sind keine Wissensbereiche lesbar.</p>
          ) : (
            collections.map((collection) => (
              <label
                key={collection.slug}
                className="flex min-h-[40px] cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm font-medium hover:bg-[var(--surface-muted)]"
              >
                <input
                  type="checkbox"
                  checked={selectedCollections.includes(collection.slug)}
                  onChange={() => onToggleCollection(collection.slug)}
                  className="h-4 w-4 flex-shrink-0 accent-[var(--accent)]"
                />
                <span className="min-w-0 flex-1 truncate">
                  {collection.name}
                  {collection.public ? <span className="ml-1 font-normal text-[var(--foreground-muted)]">(öffentlich)</span> : null}
                </span>
              </label>
            ))
          )}

          <div className="mt-1 border-t border-[var(--border)] px-2 pt-1.5 text-[11px] text-[var(--foreground-muted)]">
            Nur Bereiche, die du nutzen darfst.
          </div>
        </div>
      ) : null}
    </div>
  );
}
