'use client';

import { useRouter } from 'next/navigation';
import { BookOpen, Bot as BotIcon, Info, LogOut } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ThemeToggle } from '@/components/theme-toggle';
import { cn } from '@/lib/utils';
import type { Bot, Collection } from '@/types/weave-api';
import type { MappedError } from '@/lib/errors';
import { ErrorBanner } from '@/components/chat/error-banner';

interface SidebarProps {
  bots: Bot[] | null;
  botsError: MappedError | null;
  selectedBotId: string | null;
  onSelectBot: (botId: string) => void;
  collections: Collection[] | null;
  collectionsError: MappedError | null;
}

export function Sidebar({ bots, botsError, selectedBotId, onSelectBot, collections, collectionsError }: SidebarProps) {
  const router = useRouter();

  async function logout() {
    await fetch('/api/session/logout', { method: 'POST' }).catch(() => {});
    router.replace('/login');
    router.refresh();
  }

  return (
    <aside className="flex h-full w-80 flex-shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)]">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
        <span className="text-sm font-semibold">Weave Chat</span>
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <Button variant="ghost" size="sm" onClick={logout} aria-label="Abmelden" title="Abmelden">
            <LogOut className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--foreground-muted)]">
            <BotIcon className="h-3.5 w-3.5" aria-hidden="true" />
            Bot
          </h2>

          {/* aria-live: loading/error/empty/list are silent DOM swaps to a
              screen reader otherwise — see message-bubble.tsx's own
              "Antwort wird erzeugt…" span for the same pattern used for a
              streaming reply. */}
          <div aria-live="polite">
            {botsError ? (
              <ErrorBanner error={botsError} />
            ) : bots === null ? (
              <p className="text-xs text-[var(--foreground-muted)]">Bots werden geladen…</p>
            ) : bots.length === 0 ? (
              <p className="text-xs text-[var(--foreground-muted)]">Für dich sind keine Bots verfügbar.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {bots.map((bot) => {
                  const active = bot.id === selectedBotId;
                  return (
                    <li key={bot.id}>
                      <button
                        type="button"
                        onClick={() => onSelectBot(bot.id)}
                        aria-pressed={active}
                        className={cn(
                          'w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                          active
                            ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                            : 'border-transparent hover:bg-[var(--surface-muted)]'
                        )}
                      >
                        <div className="font-medium">{bot.name}</div>
                        {bot.description ? (
                          <div className="mt-0.5 text-xs text-[var(--foreground-muted)]">{bot.description}</div>
                        ) : null}
                        <div className="mt-1 text-[11px] text-[var(--foreground-muted)]">
                          {bot.retrieval.enabled ? 'durchsucht eine Wissensbasis' : 'ohne Wissensbasis (reines Gespräch)'}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>

        <section className="mt-6">
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--foreground-muted)]">
            <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
            Collections
          </h2>

          <p className="mb-2 flex items-start gap-1.5 text-[11px] text-[var(--foreground-muted)]">
            <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
            <span>
              Nur Information, keine Auswahl: Diese Liste zeigt, welche Collections du laut deinem Team lesen darfst
              — sie vergibt keine zusätzlichen Rechte. Weave-API nimmt aktuell keinen Collection-Filter je
              Chat-Anfrage entgegen; welche Collections ein Turn tatsächlich durchsucht hat, steht stattdessen im
              Trace der jeweiligen Antwort.
            </span>
          </p>

          {/* Same aria-live rationale as the Bot list above. */}
          <div aria-live="polite">
            {collectionsError ? (
              <ErrorBanner error={collectionsError} />
            ) : collections === null ? (
              <p className="text-xs text-[var(--foreground-muted)]">Collections werden geladen…</p>
            ) : collections.length === 0 ? (
              <p className="text-xs text-[var(--foreground-muted)]">Für dich sind keine Collections lesbar.</p>
            ) : (
              <ul className="flex flex-wrap gap-1.5">
                {collections.map((collection) => (
                  <li
                    key={collection.slug}
                    title={collection.description ?? collection.slug}
                    className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-1 text-[11px]"
                  >
                    {collection.name}
                    {collection.public ? <span className="ml-1 text-[var(--foreground-muted)]">(öffentlich)</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </aside>
  );
}
