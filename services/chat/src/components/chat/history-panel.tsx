'use client';

import { useState } from 'react';
import { History, PanelRightClose, PanelRightOpen, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ConversationSummary } from '@/types/weave-api';
import type { MappedError } from '@/lib/errors';
import { ErrorBanner } from '@/components/chat/error-banner';

interface HistoryPanelProps {
  conversations: ConversationSummary[] | null;
  conversationsError: MappedError | null;
  selectedConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onDeleteAllConversations: () => void;
}

/**
 * The conversation history, as its own collapsible column on the RIGHT —
 * separate from the left sidebar (bots + collection filter) so a long
 * history can never crowd out the controls a turn actually needs.
 */
export function HistoryPanel({
  conversations,
  conversationsError,
  selectedConversationId,
  onSelectConversation,
  onDeleteConversation,
  onDeleteAllConversations,
}: HistoryPanelProps) {
  const [open, setOpen] = useState(true);

  if (!open) {
    return (
      <aside className="flex h-full w-12 flex-shrink-0 flex-col items-center border-l border-[var(--border)] bg-[var(--surface)] py-3">
        <Button variant="ghost" size="sm" onClick={() => setOpen(true)} aria-label="Verlauf einblenden" title="Verlauf einblenden">
          <PanelRightOpen className="h-4 w-4" aria-hidden="true" />
        </Button>
        <History className="mt-3 h-4 w-4 text-[var(--foreground-muted)]" aria-hidden="true" />
      </aside>
    );
  }

  return (
    <aside className="flex h-full w-72 flex-shrink-0 flex-col border-l border-[var(--border)] bg-[var(--surface)]">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--foreground-muted)]">
          <History className="h-3.5 w-3.5" aria-hidden="true" />
          Verlauf
        </h2>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)} aria-label="Verlauf ausblenden" title="Verlauf ausblenden">
          <PanelRightClose className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {conversations && conversations.length > 0 ? (
          <button
            type="button"
            onClick={onDeleteAllConversations}
            className="mb-2 text-[11px] font-medium text-red-600 hover:underline"
          >
            Verlauf löschen
          </button>
        ) : null}

        <div aria-live="polite">
          {conversationsError ? (
            <ErrorBanner error={conversationsError} />
          ) : conversations === null ? (
            <p className="text-xs text-[var(--foreground-muted)]">Verlauf wird geladen…</p>
          ) : conversations.length === 0 ? (
            <p className="text-xs text-[var(--foreground-muted)]">Noch keine gespeicherten Konversationen.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {conversations.map((conversation) => {
                const active = conversation.id === selectedConversationId;
                return (
                  <li key={conversation.id} className="group flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => onSelectConversation(conversation.id)}
                      aria-pressed={active}
                      className={cn(
                        'min-w-0 flex-1 rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                        active
                          ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                          : 'border-transparent hover:bg-[var(--surface-muted)]'
                      )}
                    >
                      <div className="truncate font-medium">{conversation.title ?? 'Ohne Titel'}</div>
                      <div className="mt-0.5 text-[11px] text-[var(--foreground-muted)]">
                        {formatConversationTimestamp(conversation.updated_at)}
                      </div>
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
          )}
        </div>
      </div>
    </aside>
  );
}

/** `Conversation.updated_at` (ISO-8601, UTC) formatted for the history
 * list — German locale to match the rest of this UI's own copy. */
function formatConversationTimestamp(iso: string): string {
  return new Date(iso).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
}
