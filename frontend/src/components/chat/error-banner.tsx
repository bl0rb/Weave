import { AlertTriangle } from 'lucide-react';
import type { MappedError } from '@/lib/errors';

/** A finished-but-failed (or interrupted) turn's error — always the
 * mapped German sentence, with the raw upstream `detail` (if any)
 * available underneath for debugging, never as the headline message. */
export function ErrorBanner({ error }: { error: MappedError }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-1 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]"
    >
      <span className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span className="font-medium">{error.message}</span>
      </span>
      {error.detail ? <span className="pl-6 opacity-80">{error.detail}</span> : null}
    </div>
  );
}
