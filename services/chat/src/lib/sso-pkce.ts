import 'server-only';
import { createHash, randomBytes } from 'node:crypto';
import type { NextRequest, NextResponse } from 'next/server';
import { isSecureRequest } from '@/lib/session';

/**
 * The PKCE verifier that ties an SSO handoff's one-time code to the browser
 * that started the login (see sso.ts's `buildGatewayLoginUrl` for the attack
 * this closes). /api/auth/sso/start mints it and sets this cookie;
 * /api/auth/sso/callback reads it, sends it to Weave-API as `code_verifier`,
 * and deletes it.
 *
 * - httpOnly: page JavaScript never needs it.
 * - SameSite=Lax: the callback is reached by a top-level GET redirect from
 *   Weave-API's (possibly cross-site) origin, which Lax still sends with;
 *   Strict would drop it exactly there.
 * - Path-scoped to /api/auth/sso, so it rides along on nothing but the two
 *   routes that use it.
 * - 10 minutes: long enough for a slow IdP sign-in (MFA, password reset),
 *   short enough that a forgotten one doesn't linger.
 *
 * One cookie per browser, so starting a second login in another tab
 * replaces the first tab's verifier and that tab's callback then fails
 * with "please try again" — never with a session it didn't start.
 */
export const SSO_PKCE_COOKIE_NAME = 'weave_sso_pkce';
const SSO_PKCE_COOKIE_PATH = '/api/auth/sso';
const SSO_PKCE_MAX_AGE_SECONDS = 10 * 60;

// RFC 7636 section 4.1: 43-128 characters of the unreserved set. 32 random
// bytes base64url-encode to exactly 43.
const VERIFIER_PATTERN = /^[A-Za-z0-9._~-]{43,128}$/;

export function createCodeVerifier(): string {
  return randomBytes(32).toString('base64url');
}

/** RFC 7636 S256: base64url(sha256(verifier)), no padding — the 43-character
 * shape Weave-API's `_valid_code_challenge` accepts. */
export function codeChallengeFor(verifier: string): string {
  return createHash('sha256').update(verifier, 'ascii').digest('base64url');
}

export function setPkceCookie(request: NextRequest, response: NextResponse, verifier: string): void {
  response.cookies.set({
    name: SSO_PKCE_COOKIE_NAME,
    value: verifier,
    httpOnly: true,
    secure: isSecureRequest(request),
    sameSite: 'lax',
    path: SSO_PKCE_COOKIE_PATH,
    maxAge: SSO_PKCE_MAX_AGE_SECONDS,
  });
}

/** The verifier this browser carries, or `null` when there is none or it
 * isn't shaped like one this app could have minted. */
export function readPkceCookie(request: NextRequest): string | null {
  const value = request.cookies.get(SSO_PKCE_COOKIE_NAME)?.value;
  return value && VERIFIER_PATTERN.test(value) ? value : null;
}

export function clearPkceCookie(request: NextRequest, response: NextResponse): void {
  response.cookies.set({
    name: SSO_PKCE_COOKIE_NAME,
    value: '',
    httpOnly: true,
    secure: isSecureRequest(request),
    sameSite: 'lax',
    path: SSO_PKCE_COOKIE_PATH,
    maxAge: 0,
  });
}
