import 'server-only';
import type { NextRequest, NextResponse } from 'next/server';

/**
 * The ONE place the personal Weave-API bearer token is allowed to touch
 * an HTTP cookie. See weave-api-server.ts's module docstring for the full
 * reasoning on why the token lives here at all instead of in the browser.
 *
 * `httpOnly: true` is what actually keeps this promise: it makes the
 * cookie invisible to `document.cookie` and to any client-side JavaScript
 * on this origin, including this very app's own client bundle — the only
 * things that can ever read it are this Next.js server process and the
 * browser's own cookie jar sending it back on the next request. Nothing
 * in src/app/** client components imports this module; `import
 * 'server-only'` above turns any accidental future import from a 'use
 * client' file into a build-time error instead of a silent leak.
 */
export const SESSION_COOKIE_NAME = 'weave_api_token';

const THIRTY_DAYS_IN_SECONDS = 60 * 60 * 24 * 30;

function isProduction(): boolean {
  return process.env.NODE_ENV === 'production';
}

export function getSessionToken(request: NextRequest): string | null {
  return request.cookies.get(SESSION_COOKIE_NAME)?.value ?? null;
}

/**
 * `secure` only in production: over plain HTTP (local dev, a container
 * reached directly without TLS) a `Secure` cookie is silently dropped by
 * the browser, which would make the login form appear to succeed while no
 * cookie is ever actually stored. `sameSite: 'lax'` is enough here — this
 * app makes no cross-site requests that should ever carry the cookie, and
 * 'lax' still allows the plain top-level navigation from following the
 * login form's own redirect.
 */
export function setSessionCookie(response: NextResponse, token: string): void {
  response.cookies.set({
    name: SESSION_COOKIE_NAME,
    value: token,
    httpOnly: true,
    secure: isProduction(),
    sameSite: 'lax',
    path: '/',
    maxAge: THIRTY_DAYS_IN_SECONDS,
  });
}

export function clearSessionCookie(response: NextResponse): void {
  response.cookies.set({
    name: SESSION_COOKIE_NAME,
    value: '',
    httpOnly: true,
    secure: isProduction(),
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  });
}
