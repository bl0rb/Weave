import { RotateCcw, ShieldAlert } from 'lucide-react';
import type { GuardTrace } from '@/types/weave-api';

const REASON_TEXT: Record<string, string> = {
  no_context: 'Es wurden keine passenden Belege in den durchsuchbaren Collections gefunden.',
  no_collections: 'Für diese Anfrage steht keine Collection zur Verfügung, die dieser Bot durchsuchen darf.',
  // Deliberately distinct from "no_collections" above (see
  // contracts/internal-chat.md): here the bot/team combination DID have a
  // non-empty readable scope — it was this caller's own knowledge-space
  // selection (the composer's scope picker, not a sidebar anymore — see
  // scope-picker.tsx) that narrowed it to nothing. Unlike "no_collections",
  // that is fixable by the caller alone, so the message names the fix
  // directly, and `onResetScopeAndRetry` below offers to do it in one step.
  filter_excluded_all:
    'Deine Auswahl der Wissensbereiche schließt alle Collections aus, die dieser Bot für dich durchsuchen dürfte. Auswahl aufheben, um wieder alles zu durchsuchen, was dir erlaubt ist.',
};

interface GuardBannerProps {
  guard: GuardTrace;
  /** Clears the composer's knowledge-space selection and resends the last
   * question — only meaningful (and only rendered as a button) for
   * `reason: 'filter_excluded_all'`, the one guard reason the caller can
   * fix by themselves. Omitted for every other render site that has no
   * "last question" to resend (e.g. a loaded past conversation). */
  onResetScopeAndRetry?: () => void;
}

/**
 * Flags a guard-substituted reply as exactly that — a fixed text standing
 * in for a real, source-backed answer (Weave-Runtime's `no_context_reply`
 * for "no_context"/"no_collections", or its own separate fixed text for
 * "filter_excluded_all" — contracts/internal-chat.md) — so it is never
 * mistaken for an ordinary answer. Rendered ABOVE the message text, not
 * folded into it.
 */
export function GuardBanner({ guard, onResetScopeAndRetry }: GuardBannerProps) {
  if (!guard.triggered) return null;

  return (
    <div className="mb-2 flex flex-col gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning-soft)] px-3 py-2 text-xs text-[var(--warning)]">
      <span className="flex items-start gap-2">
        <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>
          <span className="font-medium">Keine Belege gefunden.</span>{' '}
          {guard.reason ? REASON_TEXT[guard.reason] ?? 'Die Antwort unten ist keine wissensbasierte Antwort.' : null}
        </span>
      </span>
      {guard.reason === 'filter_excluded_all' && onResetScopeAndRetry ? (
        <button
          type="button"
          onClick={onResetScopeAndRetry}
          className="inline-flex min-h-[40px] w-fit items-center gap-1.5 self-start rounded-md border border-[var(--warning)]/40 px-2.5 py-1 text-xs font-medium text-[var(--warning)] hover:bg-[var(--warning)]/10 sm:min-h-0"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Auswahl zurücksetzen &amp; neu fragen
        </button>
      ) : null}
    </div>
  );
}
