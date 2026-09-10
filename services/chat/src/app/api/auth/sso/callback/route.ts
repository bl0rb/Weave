import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { setSessionCookie } from '@/lib/session';
import { resolveAppBaseUrl } from '@/lib/sso';
import { GatewayError, exchangeSessionCode } from '@/lib/weave-api-server';

// Always dynamic: this handler reads a one-time query param and sets a
// cookie on every single request — there is nothing here Next.js could
// ever correctly cache or statically render.
export const dynamic = 'force-dynamic';

/**
 * GET /api/auth/sso/callback — the "own callback route" half of the
 * cross-origin SSO handoff (Weave-API backend/app/api/auth.py's own
 * "Cross-origin post-login handoff" docstring section spells out the full
 * three-step contract this and src/lib/sso.ts's `buildSsoLoginUrl`
 * implement together):
 *
 * 1. /login's "Mit SSO anmelden" link sends the browser to Weave-API's own
 *    GET /v1/auth/oidc/login?return_to=<this route's own absolute URL>.
 * 2. Weave-API runs the entire OIDC dance itself (state/PKCE/nonce
 *    against the configured provider — none of that touches this app at
 *    all) and, on success, redirects the browser BACK here with a fresh,
 *    single-use, 60-second-lived `code` query param — because `return_to`
 *    matched Weave-API's own `OIDC_POST_LOGIN_ALLOWED_URLS` allowlist.
 * 3. THIS handler exchanges that code for a real Weave-API session token
 *    by calling POST /v1/auth/session/exchange SERVER-SIDE (never from
 *    browser JS — see weave-api-server.ts's `exchangeSessionCode`), sets
 *    it as this app's OWN httpOnly cookie (`kind: 'session'`, so
 *    weave-api-server.ts's `weaveApiFetch` sends it back to Weave-API as
 *    a `Cookie` header on every later proxied call, never as a Bearer
 *    token — see that module's docstring on why the two credential kinds
 *    cannot be sent interchangeably), and redirects to the chat view.
 *
 * The raw session token exists in this process only long enough to be
 * written straight into the Set-Cookie header below — it is never
 * embedded in this response's URL, body, or any other header, so nothing
 * about it reaches the browser except inside that one httpOnly cookie
 * value, which browser-side JavaScript can't read either.
 *
 * Every failure mode redirects to /login with a `?error=<reason>` this
 * app's own login page translates into one understandable German sentence
 * (see login-form.tsx) — never a raw Weave-API status code or detail
 * string shown to the end user, and never a stack trace.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const code = request.nextUrl.searchParams.get('code');
  if (!code) {
    return redirectToLogin('missing_code');
  }

  try {
    const { sessionToken, expiresAt } = await exchangeSessionCode(code);

    // Behind a reverse proxy `request.url` carries this container's own
    // internal origin, so the browser-facing target comes from config.
    const redirectResponse = NextResponse.redirect(new URL('/', resolveAppBaseUrl()));
    setSessionCookie(request, redirectResponse, sessionToken, {
      kind: 'session',
      maxAgeSeconds: secondsUntil(expiresAt),
    });
    return redirectResponse;
  } catch (cause) {
    if (cause instanceof GatewayError && cause.kind === 'unreachable') {
      return redirectToLogin('gateway_unreachable');
    }
    // Weave-API's own exchange endpoint never distinguishes "unknown",
    // "expired", and "already used" from one another (its own
    // non-enumeration discipline) — neither does this route; every other
    // rejection (including a malformed response body) is the same
    // generic "invalid_code" as far as the end user is concerned.
    return redirectToLogin('invalid_code');
  }
}

function redirectToLogin(error: string): NextResponse {
  const loginUrl = new URL('/login', resolveAppBaseUrl());
  loginUrl.searchParams.set('error', error);
  return NextResponse.redirect(loginUrl);
}

/** Seconds between now and `expiresAtIso` (Weave-API's own
 * `SessionExchangeResponse.expires_at`), or `undefined` — falling back to
 * `setSessionCookie`'s own default — if that value is missing, malformed,
 * or already in the past by the time this runs. */
function secondsUntil(expiresAtIso: string): number | undefined {
  const expiresAtMs = Date.parse(expiresAtIso);
  if (Number.isNaN(expiresAtMs)) return undefined;
  const seconds = Math.floor((expiresAtMs - Date.now()) / 1000);
  return seconds > 0 ? seconds : undefined;
}
