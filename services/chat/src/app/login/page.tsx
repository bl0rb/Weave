import {
  isSsoLoginEnabled,
  buildSsoLoginUrl,
  isWeaveLoginEnabled,
  buildWeaveLoginUrl,
} from '@/lib/sso';
import { LoginForm } from './login-form';

// The SSO URL depends on runtime deployment environment variables. Without
// this, Next.js can prerender the login page with the localhost fallback.
export const dynamic = 'force-dynamic';

interface LoginPageProps {
  searchParams: Promise<{ error?: string | string[] }>;
}

/**
 * Server Component wrapper around the actual form (login-form.tsx):
 * whether the "Mit SSO anmelden" link even exists, and the URL it points
 * at, are both decided server-side (this app's own `WEAVE_API_OIDC_ENABLED`
 * setting — see src/lib/sso.ts's own docstring for why that, rather than
 * something read live from Weave-API) and handed down as a plain prop, so
 * the client bundle never needs to know `WEAVE_API_BASE_URL`,
 * `WEAVE_API_PUBLIC_BASE_URL`, or `APP_BASE_URL` at all. `searchParams` is
 * how a failed /api/auth/sso/callback redirect (`/login?error=...`)
 * reaches this page — see login-form.tsx for how `error` becomes a German
 * message.
 */
export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const rawError = Array.isArray(params.error) ? params.error[0] : params.error;

  const ssoLoginUrl = isSsoLoginEnabled() ? buildSsoLoginUrl() : null;
  const weaveLoginUrl = isWeaveLoginEnabled() ? buildWeaveLoginUrl() : null;

  return (
    <LoginForm
      weaveLoginUrl={weaveLoginUrl}
      ssoLoginUrl={ssoLoginUrl}
      ssoError={rawError ?? null}
    />
  );
}
