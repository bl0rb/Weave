import { ShieldAlert } from 'lucide-react';
import type { GuardTrace } from '@/types/weave-api';

const REASON_TEXT: Record<string, string> = {
  no_context: 'Es wurden keine passenden Belege in den durchsuchbaren Collections gefunden.',
  no_collections: 'Für diese Anfrage steht keine Collection zur Verfügung, die dieser Bot durchsuchen darf.',
};

/**
 * Flags a guard-substituted reply as exactly that — Weave-Runtime's fixed
 * `no_context_reply` text standing in for a real, source-backed answer —
 * so it is never mistaken for an ordinary answer. Rendered ABOVE the
 * message text, not folded into it.
 */
export function GuardBanner({ guard }: { guard: GuardTrace }) {
  if (!guard.triggered) return null;

  return (
    <div className="mb-2 flex items-start gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning-soft)] px-3 py-2 text-xs text-[var(--warning)]">
      <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
      <span>
        <span className="font-medium">Keine Belege gefunden.</span>{' '}
        {guard.reason ? REASON_TEXT[guard.reason] ?? 'Die Antwort unten ist keine wissensbasierte Antwort.' : null}
      </span>
    </div>
  );
}
