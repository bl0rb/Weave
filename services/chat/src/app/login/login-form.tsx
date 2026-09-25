'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, KeyRound, LogIn } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useI18n } from '@/i18n/provider';
import { LanguageSwitch } from '@/i18n/language-switch';
import type { MessageKey } from '@/i18n/messages';
import type { MappedError } from '@/lib/errors';

// Keyed by the `error` query param /api/auth/sso/callback redirects here
// with — see that route's own docstring for exactly when each fires.
// Deliberately separate from src/lib/errors.ts's `MESSAGES` table: that
// one maps Weave-API HTTP statuses encountered mid-chat, this one maps
// SSO-handoff-specific failure reasons that only ever occur on this page.
const SSO_ERROR_KEYS: Record<string, MessageKey> = {
  missing_code: 'chat.login.ssoError.missingCode',
  invalid_code: 'chat.login.ssoError.invalidCode',
  gateway_unreachable: 'chat.login.ssoError.gatewayUnreachable',
};

interface LoginFormProps {
  /** Weave-API's own GET /v1/auth/ingest/login URL (src/lib/sso.ts's
   * `buildWeaveLoginUrl`), or `null` when this deployment has no
   * federated login configured. When present this is the PRIMARY action:
   * it leads to Weave-Ingest's own sign-in page, where a person uses
   * whatever method the admin set up for them — a password or any
   * configured OIDC connection — and comes back signed in here. The token
   * form below stays for scripts and for anyone who prefers it. */
  weaveLoginUrl: string | null;
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
 * /login — the only page that ever asks for the raw Weave-API token, plus
 * up to two links into the cross-origin handoff (see
 * /api/auth/sso/callback's own docstring for the full contract that
 * starts): `weaveLoginUrl`, the federated login through Weave-Ingest, and
 * `ssoLoginUrl`, a gateway pointed straight at one OIDC provider. Both end
 * at the same callback with a one-time code; an operator configures one or
 * the other, so in practice at most one appears. Submits the Personal-API-Token to our OWN server
 * (POST /api/session/login), which proves the token against Weave-API's
 * GET /v1/bots and only then sets the httpOnly cookie — see that Route
 * Handler and src/lib/session.ts for why the token is handled that way.
 * This form never stores the token itself anywhere (no localStorage, no
 * component state survives the submit) — it is a value the browser holds
 * only long enough to send it once. The SSO link, by contrast, is a plain
 * top-level navigation to Weave-API's own origin — never a `fetch()` —
 * since it has to carry the browser through a real OIDC redirect dance.
 */
export function LoginForm({ weaveLoginUrl, ssoLoginUrl, ssoError }: LoginFormProps) {
  const router = useRouter();
  const { t } = useI18n();
  const [token, setToken] = useState('');
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // A token-form submission failure (set below) always takes priority
  // over a stale `ssoError` still sitting in the URL from an earlier
  // failed SSO attempt — the user has since tried something else.
  const displayError = formError ?? (ssoError ? t(SSO_ERROR_KEYS[ssoError] ?? 'chat.login.ssoError.default') : null);

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
      setFormError(body?.message ?? t('chat.login.genericFailure'));
    } catch {
      setFormError(t('chat.login.networkFailure'));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--bg)] px-4 py-12 text-[var(--ink)]">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-[var(--radius-card)] bg-[var(--accent-soft)] text-[var(--accent)]">
            <KeyRound className="h-6 w-6" aria-hidden="true" />
          </div>
          <span className="text-lg font-semibold">{t('common.appTitle')}</span>
        </div>

        <div className="rounded-[var(--radius-card)] border border-[var(--line)] bg-[var(--surface)] p-6 shadow-sm sm:p-8">
          <h1 className="text-lg font-semibold">{t('chat.login.heading')}</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {weaveLoginUrl ? t('chat.login.subtitleFederated') : t('chat.login.subtitleToken')}
          </p>

          {weaveLoginUrl ? (
            <>
              <a href={weaveLoginUrl} className={cn(buttonVariants(), 'mt-6 w-full')}>
                <LogIn className="h-4 w-4" aria-hidden="true" />
                {t('chat.login.withWeave')}
              </a>

              <div className="my-5 flex items-center gap-3 text-xs text-[var(--muted)]" aria-hidden="true">
                <span className="h-px flex-1 bg-[var(--line)]" />
                {t('chat.login.orWithToken')}
                <span className="h-px flex-1 bg-[var(--line)]" />
              </div>
            </>
          ) : null}

          <form onSubmit={onSubmit} className={cn('flex flex-col gap-4', weaveLoginUrl ? '' : 'mt-6')}>
            <div>
              <label htmlFor="token" className="mb-1.5 block text-sm font-medium">
                {t('chat.login.tokenLabel')}
              </label>
              <input
                id="token"
                name="token"
                type="password"
                autoComplete="off"
                // Not focused when there is a primary button above it: the
                // keyboard should land on the action most people want.
                autoFocus={!weaveLoginUrl}
                required
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="wt_..."
                className="h-10 w-full rounded-[var(--radius-control)] border border-[var(--line-2)] bg-[var(--surface)] px-3 text-sm outline-none transition placeholder:text-[var(--muted)] focus-visible:border-[var(--accent)] focus-visible:ring-[3px] focus-visible:ring-[var(--accent-soft)]"
              />
            </div>

            {displayError ? (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-[var(--radius-control)] border border-[var(--err)]/30 bg-[var(--err-bg)] px-3 py-2.5 text-sm text-[var(--err)]"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
                <span>{displayError}</span>
              </div>
            ) : null}

            <Button type="submit" disabled={pending || !token.trim()} className="mt-1 w-full">
              {pending ? t('chat.login.submitPending') : t('chat.login.submit')}
            </Button>
          </form>

          {ssoLoginUrl ? (
            <>
              <div className="my-5 flex items-center gap-3 text-xs text-[var(--muted)]" aria-hidden="true">
                <span className="h-px flex-1 bg-[var(--line)]" />
                {t('chat.login.or')}
                <span className="h-px flex-1 bg-[var(--line)]" />
              </div>

              <a href={ssoLoginUrl} className={cn(buttonVariants({ variant: 'outline' }), 'w-full')}>
                <LogIn className="h-4 w-4" aria-hidden="true" />
                {t('chat.login.withSso')}
              </a>
            </>
          ) : null}
        </div>

        <div className="mt-6 flex justify-center">
          <LanguageSwitch />
        </div>
      </div>
    </main>
  );
}
