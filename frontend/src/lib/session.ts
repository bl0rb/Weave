import 'server-only';
import type { NextRequest, NextResponse } from 'next/server';

/**
 * The ONE place either of this UI's own credential kinds is allowed to
 * touch an HTTP cookie. See weave-api-server.ts's module docstring for the
 * full reasoning on why a credential lives here at all instead of in the
 * browser.
 *
 * `httpOnly: true` is what actually keeps this promise: it makes the
 * cookie invisible to `document.cookie` and to any client-side JavaScript
 * on this origin, including this very app's own client bundle — the only
 * things that can ever read it are this Next.js server process and the
 * browser's own cookie jar sending it back on the next request. Nothing
 * in src/app/** client components imports this module; `import
 * 'server-only'` above turns any accidental future import from a 'use
 * client' file into a build-time error instead of a silent leak.
 *
 * Two credential kinds share this one cookie pair (see `SessionKind`
 * below) — a Personal-API-Token typed into /login, or a Weave-API browser
 * session token obtained via the SSO handoff (/api/auth/sso/callback,
 * Weave-API backend/app/api/auth.py's POST /v1/auth/session/exchange).
 * Both are equally sensitive opaque bearer values from this app's point of
 * view; only the HEADER shape Weave-API expects them back as differs (see
 * weave-api-server.ts), which is why the kind travels alongside the value
 * instead of being inferred from it.
 */
export const SESSION_COOKIE_NAME = 'weave_api_token';

// Companion cookie to SESSION_COOKIE_NAME, set/cleared together with it
// (never independently) — see `setSessionCookie`/`clearSessionCookie`
// below. A cookie set by a version of this app before this file existed
// carries no kind cookie at all; `getSessionCredential` treats that
// exactly like `'bearer'`, which is what every such cookie always was, so
// nothing about the Personal-API-Token path changes for an existing
// session.
export const SESSION_KIND_COOKIE_NAME = 'weave_api_token_kind';

export type SessionKind = 'bearer' | 'session';

export interface SessionCredential {
  kind: SessionKind;
  value: string;
}

const THIRTY_DAYS_IN_SECONDS = 60 * 60 * 24 * 30;

function isProduction(): boolean {
  return process.env.NODE_ENV === 'production';
}

/** The credential this browser is carrying, or `null` if there is none at
 * all. `kind` defaults to `'bearer'` whenever the value cookie is present
 * without its kind companion — see this module's docstring on why that is
 * the correct default, not just a fallback. */
export function getSessionCredential(request: NextRequest): SessionCredential | null {
  const value = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  if (!value) return null;
  const rawKind = request.cookies.get(SESSION_KIND_COOKIE_NAME)?.value;
  const kind: SessionKind = rawKind === 'session' ? 'session' : 'bearer';
  return { kind, value };
}

/**
 * `secure` only in production: over plain HTTP (local dev, a container
 * reached directly without TLS) a `Secure` cookie is silently dropped by
 * the browser, which would make the login form appear to succeed while no
 * cookie is ever actually stored. `sameSite: 'lax'` is enough here — this
 * app makes no cross-site requests that should ever carry the cookie, and
 * 'lax' still allows the plain top-level navigation from following the
 * login form's own redirect (or the SSO callback's own redirect to `/`).
 *
 * `maxAgeSeconds` defaults to a flat 30 days for a Personal-API-Token
 * (that credential has no server-known expiry this route could read) but
 * should be passed explicitly for an SSO session — the callback route
 * knows Weave-API's own `expires_at` for that exact token and should never
 * let this cookie outlive it.
 */
export function setSessionCookie(
  response: NextResponse,
  token: string,
  options: { kind?: SessionKind; maxAgeSeconds?: number } = {}
): void {
  const kind = options.kind ?? 'bearer';
  const maxAge = options.maxAgeSeconds ?? THIRTY_DAYS_IN_SECONDS;
  const shared = {
    httpOnly: true,
    secure: isProduction(),
    sameSite: 'lax' as const,
    path: '/',
    maxAge,
  };
  response.cookies.set({ name: SESSION_COOKIE_NAME, value: token, ...shared });
  response.cookies.set({ name: SESSION_KIND_COOKIE_NAME, value: kind, ...shared });
}

export function clearSessionCookie(response: NextResponse): void {
  const shared = {
    httpOnly: true,
    secure: isProduction(),
    sameSite: 'lax' as const,
    path: '/',
    maxAge: 0,
  };
  response.cookies.set({ name: SESSION_COOKIE_NAME, value: '', ...shared });
  response.cookies.set({ name: SESSION_KIND_COOKIE_NAME, value: '', ...shared });
}
