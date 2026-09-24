'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ApiError, apiJson } from '@/lib/api';
import type { SetupStatusResponse } from '@/lib/auth-types';
import { Button } from '@/components/ui/button';
import { AuthField, AuthPageSpinner, AuthShell, FormError } from '@/components/auth/auth-card';
import { useI18n } from '@/i18n/provider';

export default function SetupPage() {
  const router = useRouter();
  const { t } = useI18n();
  const [checking, setChecking] = useState(true);

  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // If setup is already done, this page must not exist — go to /login.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const status = await apiJson<SetupStatusResponse>('/api/v1/auth/setup-status', {
          skipAuthRedirect: true,
        });
        if (cancelled) return;
        if (!status.needs_setup) {
          router.replace('/login');
          return;
        }
      } catch {
        // Backend unreachable — show the form; the POST will surface the error.
      }
      if (!cancelled) setChecking(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setError(null);

    if (password.length < 8) {
      setError(t('portal.setup.passwordTooShort'));
      return;
    }
    if (password !== confirm) {
      setError(t('portal.setup.passwordMismatch'));
      return;
    }

    setPending(true);
    try {
      await apiJson('/api/v1/auth/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, email, password }),
        skipAuthRedirect: true,
      });
      // Session cookie is set by the response — enter the app.
      window.location.assign('/');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : t('portal.setup.serviceUnreachable'));
      setPending(false);
    }
  }

  if (checking) return <AuthPageSpinner />;

  return (
    <AuthShell
      title={t('portal.setup.title')}
      subtitle={t('portal.setup.subtitle')}
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <AuthField
          id="username"
          label={t('common.username')}
          name="username"
          autoComplete="username"
          required
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <AuthField
          id="email"
          label={t('common.email')}
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <AuthField
          id="password"
          label={t('common.password')}
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          placeholder={t('portal.setup.passwordPlaceholder')}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <AuthField
          id="confirm-password"
          label={t('portal.setup.confirmPasswordLabel')}
          name="confirm-password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />

        <FormError message={error} />

        <Button type="submit" disabled={pending} className="mt-1 w-full rounded-xl">
          {pending ? t('portal.setup.submitting') : t('portal.setup.submit')}
        </Button>
      </form>
    </AuthShell>
  );
}
