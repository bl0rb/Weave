'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ExternalLink } from 'lucide-react';
import { ApiError, apiJson } from '@/lib/api';
import { resolveApiBaseUrl } from '@/lib/api-base';
import type { ListResponse, PublicProvider, SetupStatusResponse } from '@/lib/auth-types';
import { Button } from '@/components/ui/button';
import { AuthField, AuthPageSpinner, AuthShell, FormError } from '@/components/auth/auth-card';

/**
 * Where a completed sign-in continues when another Weave service sent the
 * user here (`?handoff=1`): the backend mints a one-time code and redirects
 * the browser onward to the one callback URL it is configured with. `state`
 * is opaque to us — it belongs to the service that started the flow, which
 * compares it against its own signed cookie.
 */
function handoffStartUrl(state: string | null): string {
  const query = state ? `?${new URLSearchParams({ state }).toString()}` : '';
  return `${resolveApiBaseUrl()}/api/v1/auth/handoff/start${query}`;
}

interface Handoff {
  state: string | null;
}

function safeReturnTo(value: string | null): string | null {
  return value?.startsWith('/') && !value.startsWith('//') ? value : null;
}

export default function LoginPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [providers, setProviders] = useState<PublicProvider[]>([]);
  // Read from window.location rather than useSearchParams() on purpose:
  // this is one client component with no Suspense boundary around it, and
  // useSearchParams() would force one (it opts the route out of static
  // prerendering). The value is only ever needed after mount anyway.
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [returnTo, setReturnTo] = useState<string | null>(null);

  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  // First run must go through /setup instead; also load enabled OIDC providers.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const params = new URLSearchParams(window.location.search);
      const requestedHandoff =
        params.get('handoff') === '1' ? { state: params.get('handoff_state') } : null;
      if (!cancelled) setReturnTo(safeReturnTo(params.get('returnTo')));
      if (!cancelled) setHandoff(requestedHandoff);
      const timeout = window.setTimeout(() => {
        if (!cancelled) {
          setChecking(false);
          setCheckError('Die Anmeldung konnte nicht rechtzeitig geprüft werden. Bitte versuche es erneut.');
        }
      }, 10000);

      if (requestedHandoff) {
        // Already signed in here? Then there is nothing to ask: continue
        // straight back to whoever sent us. This is what makes a second
        // Weave app feel like part of the same session rather than a
        // second login.
        try {
          await apiJson('/api/v1/auth/me', { skipAuthRedirect: true });
          window.location.assign(handoffStartUrl(requestedHandoff.state));
          return;
        } catch {
          // Not signed in (or backend unreachable) — show the form.
        }
      }

      try {
        const status = await apiJson<SetupStatusResponse>('/api/v1/auth/setup-status', {
          skipAuthRedirect: true,
        });
        if (cancelled) return;
        if (status.needs_setup) {
          router.replace('/setup');
          return;
        }
      } catch {
        // Backend unreachable — show the form; the POST will surface the error.
      }
      if (!cancelled) setChecking(false);
      window.clearTimeout(timeout);

      try {
        const list = await apiJson<ListResponse<PublicProvider>>('/api/v1/auth/providers', {
          skipAuthRedirect: true,
        });
        if (!cancelled) setProviders(list.items);
      } catch {
        // Providers are optional — local login still works without them.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [retry, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setError(null);
    setPending(true);

    try {
      await apiJson('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier, password }),
        skipAuthRedirect: true,
      });
      // Session cookie is set by the response — enter the app, or hand the
      // freshly authenticated session back to whoever sent us here.
      window.location.assign(handoff ? handoffStartUrl(handoff.state) : returnTo || '/');
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Benutzername oder Passwort ist falsch.');
      } else if (err instanceof ApiError && err.status === 429) {
        setError('Zu viele Versuche. Bitte versuche es gleich erneut.');
      } else if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError('Der Dienst ist nicht erreichbar. Bitte versuche es erneut.');
      }
      setPending(false);
    }
  }

  /** OIDC must be a full-page navigation so the IdP redirect chain works.
   * The handoff request rides along, so a person sent here by another
   * Weave app can sign in with ANY configured provider and still end up
   * back there — that is the whole point of routing every login through
   * this one page. */
  function loginWithProvider(slug: string) {
    const params = new URLSearchParams();
    if (handoff) {
      params.set('handoff', '1');
      if (handoff.state) params.set('handoff_state', handoff.state);
    }
    const query = params.toString();
    window.location.assign(
      `${resolveApiBaseUrl()}/api/v1/auth/oidc/${slug}/authorize${query ? `?${query}` : ''}`,
    );
  }

  if (checking) return <AuthPageSpinner />;

  return (
    <AuthShell
      title="Anmelden"
      subtitle={
        handoff
          ? 'Melde dich mit deinem Weave-Konto an. Danach geht es zurück zum Chat.'
          : 'Willkommen im Wissensportal. Melde dich mit deinem Weave-Konto an.'
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        {checkError && (
          <div className="space-y-2">
            <FormError message={checkError} />
            <Button type="button" variant="outline" onClick={() => { setCheckError(null); setChecking(true); setRetry((value) => value + 1); }}>
              Erneut prüfen
            </Button>
          </div>
        )}
        <AuthField
          id="identifier"
          label="Benutzername oder E-Mail"
          name="username"
          autoComplete="username"
          required
          autoFocus
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
        />
        <AuthField
          id="password"
          label="Passwort"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <FormError message={error} />

        <Button type="submit" disabled={pending} className="mt-1 w-full rounded-xl">
          {pending ? 'Anmeldung läuft …' : 'Anmelden'}
        </Button>
      </form>

      {providers.length > 0 && (
        <div className="mt-6">
          <div className="flex items-center gap-3" aria-hidden="true">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
              oder über eure Organisation
            </span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>
          <div className="mt-4 flex flex-col gap-2">
            {providers.map((provider) => (
              <Button
                key={provider.slug}
                type="button"
                variant="outline"
                onClick={() => loginWithProvider(provider.slug)}
                className="w-full rounded-xl"
              >
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                {provider.display_name}
              </Button>
            ))}
          </div>
        </div>
      )}
    </AuthShell>
  );
}
