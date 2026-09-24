'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { History, LogOut, Plus, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ThemeToggle } from '@/components/theme-toggle';
import { cn } from '@/lib/utils';
import type { ConversationSummary } from '@/types/weave-api';
import type { MappedError } from '@/lib/errors';
import { ErrorBanner } from '@/components/chat/error-banner';

interface RailProps {
  conversations: ConversationSummary[] | null;
  conversationsError: MappedError | null;
  selectedConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onDeleteAllConversations: () => void;
  onNewConversation: () => void;
  newConversationDisabled: boolean;
  /** Drawer state below the 900px breakpoint (see globals.css's
   * `.chat-rail`/`.chat-rail.is-open`) — above it the rail is always
   * visible and these are unused. */
  open: boolean;
  onClose: () => void;
}

const GROUP_LABELS = ['Heute', 'Diese Woche', 'Älter'] as const;
type GroupLabel = (typeof GROUP_LABELS)[number];

/** Buckets the history list by recency for the grouped headings the design
 * calls for — "Heute" for anything updated since local midnight, "Diese
 * Woche" for the six days before that, "Älter" for the rest. A pure
 * function of `now` (exported for rail.test.tsx, so this stays directly
 * testable without faking the system clock) rather than reading
 * `new Date()` internally. */
export function groupConversations(
  conversations: ConversationSummary[],
  now: Date
): Array<[GroupLabel, ConversationSummary[]]> {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfWeek = startOfToday - 6 * 24 * 60 * 60 * 1000;

  const buckets: Record<GroupLabel, ConversationSummary[]> = { Heute: [], 'Diese Woche': [], Älter: [] };
  for (const conversation of conversations) {
    const updated = new Date(conversation.updated_at).getTime();
    if (updated >= startOfToday) buckets.Heute.push(conversation);
    else if (updated >= startOfWeek) buckets['Diese Woche'].push(conversation);
    else buckets.Älter.push(conversation);
  }
  return GROUP_LABELS.map((label) => [label, buckets[label]] as [GroupLabel, ConversationSummary[]]).filter(
    ([, items]) => items.length > 0
  );
}

/**
 * The left rail: "Neues Gespräch", the conversation history (grouped by
 * recency), and a footer with the account controls this app already had in
 * its old sidebar header (theme toggle, logout) — merged from the former
 * separate right-hand HistoryPanel, which this component replaces. Becomes
 * an off-canvas drawer below 900px (see globals.css's `.chat-rail`), driven
 * by `open`/`onClose` from chat-app.tsx.
 */
export function Rail({
  conversations,
  conversationsError,
  selectedConversationId,
  onSelectConversation,
  onDeleteConversation,
  onDeleteAllConversations,
  onNewConversation,
  newConversationDisabled,
  open,
  onClose,
}: RailProps) {
  const router = useRouter();

  // Escape closes the drawer on the narrow (<900px) layout only — `open`
  // is always true at the wider layout (see chat-app.tsx), where the rail
  // is a permanent column and there is nothing to dismiss.
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  async function logout() {
    await fetch('/api/session/logout', { method: 'POST' }).catch(() => {});
    router.replace('/login');
    router.refresh();
  }

  const groups = conversations && conversations.length > 0 ? groupConversations(conversations, new Date()) : [];

  return (
    <>
      <div
        className={cn('chat-rail-backdrop', open && 'is-open')}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={cn(
          'chat-rail flex h-full min-h-0 w-full flex-col border-r border-[var(--border)] bg-[var(--surface)]',
          open && 'is-open'
        )}
        aria-label="Gespräche"
      >
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
          <span className="text-sm font-semibold">Weave Chat</span>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label="Gespräche schließen"
            className="chat-mobile-menu hidden"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>

        <div className="px-3 pt-3">
          <Button
            type="button"
            variant="outline"
            onClick={onNewConversation}
            disabled={newConversationDisabled}
            className="w-full justify-start gap-2"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Neues Gespräch
          </Button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-3" aria-label="Verlauf">
          {conversations && conversations.length > 0 ? (
            <button
              type="button"
              onClick={onDeleteAllConversations}
              className="mb-2 text-[11px] font-medium text-[var(--danger)] hover:underline"
            >
              Verlauf löschen
            </button>
          ) : null}

          <div aria-live="polite">
            {conversationsError ? (
              <ErrorBanner error={conversationsError} />
            ) : conversations === null ? (
              <p className="flex items-center gap-1.5 px-1 text-xs text-[var(--foreground-muted)]">
                <History className="h-3.5 w-3.5" aria-hidden="true" />
                Verlauf wird geladen…
              </p>
            ) : conversations.length === 0 ? (
              <p className="px-1 text-xs text-[var(--foreground-muted)]">Noch keine gespeicherten Konversationen.</p>
            ) : (
              groups.map(([label, items]) => (
                <div key={label} className="mb-1">
                  <p className="px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-[var(--foreground-muted)] first:pt-0">
                    {label}
                  </p>
                  <ul className="flex flex-col gap-1">
                    {items.map((conversation) => {
                      const active = conversation.id === selectedConversationId;
                      return (
                        <li key={conversation.id} className="group flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => onSelectConversation(conversation.id)}
                            aria-pressed={active}
                            aria-current={active ? 'page' : undefined}
                            className={cn(
                              'min-w-0 flex-1 truncate rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                              active
                                ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                                : 'border-transparent hover:bg-[var(--surface-muted)]'
                            )}
                          >
                            {conversation.title ?? 'Ohne Titel'}
                          </button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onDeleteConversation(conversation.id)}
                            aria-label="Konversation löschen"
                            title="Konversation löschen"
                            className="flex-shrink-0"
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))
            )}
          </div>
        </nav>

        <div className="mt-auto flex items-center justify-between gap-2 border-t border-[var(--border)] px-3 py-3">
          <span className="text-xs text-[var(--foreground-muted)]">Angemeldet</span>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <Button variant="ghost" size="sm" onClick={logout} aria-label="Abmelden" title="Abmelden">
              <LogOut className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>
      </aside>
    </>
  );
}
