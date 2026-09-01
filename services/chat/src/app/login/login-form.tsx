'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, KeyRound, LogIn } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { MappedError } from '@/lib/errors';

// Keyed by the `error` query param /api/auth/sso/callback redirects here
// with — see that route's own docstring for exactly when each fires.
// Deliberately separate from src/lib/errors.ts's `MESSAGES` table: that
// one maps Weave-API HTTP statuses encountered mid-chat, this one maps
// SSO-handoff-specific failure reasons that only ever occur on this page.
const SSO_ERROR_MESSAGES: Record<string, string> = {
  missing_code: 'Die SSO-Anmeldung ist ohne Code vom Gateway zurückgekommen. Bitte versuche es erneut.',
  invalid_code: 'Der SSO-Anmeldevorgang ist abgelaufen oder ungültig. Bitte versuche es erneut.',
  gateway_unreachable: 'Weave-API war während der SSO-Anmeldung nicht erreichbar. Bitte versuche es in Kürze erneut.',
};
const DEFAULT_SSO_ERROR_MESSAGE =
  'Die Anmeldung über SSO ist fehlgeschlagen. Bitte versuche es erneut oder melde dich mit deinem Personal-Token an.';

interface LoginFormProps {
  /** Weave-API's own GET /v1/auth/oidc/login URL (src/lib/sso.ts's
   * `buildSsoLoginUrl`), or `null` when this deployment's own
   * `WEAVE_API_OIDC_ENABLED` setting says the gateway has no OIDC
   * provider configured — see that module's docstring on why this is
   * this app's OWN setting rather than something read live from
   * Weave-API. `null` hides the SSO button entirely; the Personal-Token
   * form below is unaffected either way. */
  ssoLoginUrl: string | null;
  /** The `error` query param from a failed /api/auth/sso/callback
   * redirect, or `null` on a plain, error-free page load. */
  ssoError: string | null;
}

/**
 * /login — the only page that ever asks for the raw Weave-API token, PLUS
 * (when `ssoLoginUrl` is non-null) a link into the cross-origin SSO
 * handoff (see /api/auth/sso/callback's own docstring for the full
 * contract that starts). Submits the Personal-API-Token to our OWN server
 * (POST /api/session/login), which proves the token against Weave-API's
 * GET /v1/bots and only then sets the httpOnly cookie — see that Route
 * Handler and src/lib/session.ts for why the token is handled that way.
 * This form never stores the token itself anywhere (no localStorage, no
 * component state survives the submit) — it is a value the browser holds
 * only long enough to send it once. The SSO link, by contrast, is a plain
 * top-level navigation to Weave-API's own origin — never a `fetch()` —
 * since it has to carry the browser through a real OIDC redirect dance.
 */
export function LoginForm({ ssoLoginUrl, ssoError }: LoginFormProps) {
  const router = useRouter();
  const [token, setToken] = useState('');
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // A token-form submission failure (set below) always takes priority
  // over a stale `ssoError` still sitting in the URL from an earlier
  // failed SSO attempt — the user has since tried something else.
  const displayError = formError ?? (ssoError ? (SSO_ERROR_MESSAGES[ssoError] ?? DEFAULT_SSO_ERROR_MESSAGE) : null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (pending) return;
    setFormError(null);
    setPending(true);

    try {
      const res = await fetch('/api/session/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });

      if (res.ok) {
        router.replace('/');
        router.refresh();
        return;
      }

      const body = (await res.json().catch(() => null)) as MappedError | null;
      setFormError(body?.message ?? 'Anmeldung fehlgeschlagen. Bitte versuche es erneut.');
    } catch {
      setFormError('Die Anmeldeseite konnte den Server nicht erreichen. Bitte versuche es erneut.');
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--background)] px-4 py-12 text-[var(--foreground)]">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--accent-soft)] text-[var(--accent)]">
            <KeyRound className="h-6 w-6" aria-hidden="true" />
          </div>
          <span className="text-lg font-semibold">Weave Chat</span>
        </div>

        <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-sm sm:p-8">
          <h1 className="text-lg font-semibold">Anmelden</h1>
          <p className="mt-1 text-sm text-[var(--foreground-muted)]">
            Melde dich mit deinem persönlichen Weave-API-Token an. Es wird ausschließlich serverseitig als
            httpOnly-Cookie gespeichert — nie im Browser-JavaScript.
          </p>

          <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4">
            <div>
              <label htmlFor="token" className="mb-1.5 block text-sm font-medium">
                Personal-API-Token
              </label>
              <input
                id="token"
                name="token"
                type="password"
                autoComplete="off"
                autoFocus
                required
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="wt_..."
                className="h-10 w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 text-sm outline-none transition placeholder:text-[var(--foreground-muted)] focus-visible:border-[var(--accent)]"
              />
            </div>

            {displayError ? (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-xl border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-3 py-2.5 text-sm text-[var(--danger)]"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
                <span>{displayError}</span>
              </div>
            ) : null}

            <Button type="submit" disabled={pending || !token.trim()} className="mt-1 w-full">
              {pending ? 'Wird geprüft…' : 'Anmelden'}
            </Button>
          </form>

          {ssoLoginUrl ? (
            <>
              <div className="my-5 flex items-center gap-3 text-xs text-[var(--foreground-muted)]" aria-hidden="true">
                <span className="h-px flex-1 bg-[var(--border)]" />
                oder
                <span className="h-px flex-1 bg-[var(--border)]" />
              </div>

              <a href={ssoLoginUrl} className={cn(buttonVariants({ variant: 'outline' }), 'w-full')}>
                <LogIn className="h-4 w-4" aria-hidden="true" />
                Mit SSO anmelden
              </a>
            </>
          ) : null}
        </div>
      </div>
    </main>
  );
}
