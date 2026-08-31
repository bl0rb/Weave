'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, KeyRound } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { MappedError } from '@/lib/errors';

/**
 * /login — the only page that ever asks for the raw Weave-API token.
 * Submits it to our OWN server (POST /api/session/login), which proves
 * the token against Weave-API's GET /v1/bots and only then sets the
 * httpOnly cookie — see that Route Handler and src/lib/session.ts for
 * why the token is handled that way. This form never stores the token
 * itself anywhere (no localStorage, no component state survives the
 * submit) — it is a value the browser holds only long enough to send it
 * once.
 */
export default function LoginPage() {
  const router = useRouter();
  const [token, setToken] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (pending) return;
    setError(null);
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
      setError(body?.message ?? 'Anmeldung fehlgeschlagen. Bitte versuche es erneut.');
    } catch {
      setError('Die Anmeldeseite konnte den Server nicht erreichen. Bitte versuche es erneut.');
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

            {error ? (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-xl border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-3 py-2.5 text-sm text-[var(--danger)]"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
                <span>{error}</span>
              </div>
            ) : null}

            <Button type="submit" disabled={pending || !token.trim()} className="mt-1 w-full">
              {pending ? 'Wird geprüft…' : 'Anmelden'}
            </Button>
          </form>
        </div>
      </div>
    </main>
  );
}
