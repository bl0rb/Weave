import { RotateCcw, ShieldAlert } from 'lucide-react';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import type { GuardTrace } from '@/types/weave-api';

const REASON_KEY: Record<string, MessageKey> = {
  no_context: 'chat.guard.reason.noContext',
  no_collections: 'chat.guard.reason.noCollections',
  // Deliberately distinct from "no_collections" above (see
  // contracts/internal-chat.md): here the bot/team combination DID have a
  // non-empty readable scope — it was this caller's own knowledge-space
  // selection (the composer's scope picker, not a sidebar anymore — see
  // scope-picker.tsx) that narrowed it to nothing. Unlike "no_collections",
  // that is fixable by the caller alone, so the message names the fix
  // directly, and `onResetScopeAndRetry` below offers to do it in one step.
  filter_excluded_all: 'chat.guard.reason.filterExcludedAll',
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
  const { t } = useI18n();
  if (!guard.triggered) return null;

  return (
    <div className="mb-2 flex flex-col gap-2 rounded-[var(--radius-control)] border border-[var(--warn)]/30 bg-[var(--warn-bg)] px-3 py-2 text-xs text-[var(--warn)]">
      <span className="flex items-start gap-2">
        <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>
          <span className="font-medium">{t('chat.guard.headline')}</span>{' '}
          {guard.reason ? t(REASON_KEY[guard.reason] ?? 'chat.guard.reason.unknown') : null}
        </span>
      </span>
      {guard.reason === 'filter_excluded_all' && onResetScopeAndRetry ? (
        <button
          type="button"
          onClick={onResetScopeAndRetry}
          className="inline-flex min-h-[40px] w-fit items-center gap-1.5 self-start rounded-[var(--radius-control)] border border-[var(--warn)]/40 px-2.5 py-1 text-xs font-medium text-[var(--warn)] hover:bg-[var(--warn)]/10 sm:min-h-0"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          {t('chat.guard.resetAndRetry')}
        </button>
      ) : null}
    </div>
  );
}
