import 'server-only';
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { mappedError } from '@/lib/errors';
import { resolveAppBaseUrl } from '@/lib/sso';

/**
 * CSRF guard for every state-changing Route Handler under src/app/api
 * (POST/PUT/PATCH/DELETE). Every one of them authenticates by this app's
 * own `SameSite=Lax` session cookie alone, and Lax still lets a cross-site
 * `<form method="post">` deliver a body to a route that never looks at the
 * body's type — `request.json()` parses a `text/plain` form post just as
 * happily — so without this, any page could log a victim into an
 * attacker's own token (/api/session/login) or log them out.
 *
 * Applied per route rather than in src/middleware.ts on purpose: the
 * middleware's matcher is `/` only, and widening it to /api would put a
 * redirect-to-/login cookie check in front of routes that must answer JSON
 * (and /login's own POST, which has no cookie yet) — one explicit call at
 * the top of each handler is smaller and testable next to that handler.
 *
 * Allowed:
 * - `Origin` equal to this app's configured public origin
 *   (`resolveAppBaseUrl`), OR to the origin the request itself was
 *   addressed to (`X-Forwarded-Proto`/`X-Forwarded-Host`, else `Host`).
 *   The second one keeps token login working for a deployment reached at
 *   an address other than `APP_BASE_URL` (the compose default is
 *   localhost:3001, routinely opened via the server's own IP), and is safe:
 *   a cross-site page controls `Origin` but not the `Host` the victim's
 *   browser sends, and it can't add `X-Forwarded-*` without a CORS
 *   preflight this app never answers.
 * - No `Origin`, but `Sec-Fetch-Site: same-origin` (or `none`, a
 *   user-initiated navigation).
 * - Neither header at all: not a browser. Every current browser sends
 *   `Origin` on a cross-origin POST/PUT/PATCH/DELETE and `Sec-Fetch-Site`
 *   on every request, so a request without both can't be a cross-site
 *   forgery; the desktop app talks to Weave-API directly, never to these
 *   routes, but scripted callers holding the cookie keep working. For a
 *   route with a JSON body, `json: true` below still closes even an old
 *   browser's header-less form post, because a form can't send
 *   `application/json` without that same preflight.
 *
 * `Origin: null` (sandboxed frame, opaque redirect) is never allowed.
 */
export function rejectCrossSiteRequest(request: NextRequest, options: { json?: boolean } = {}): NextResponse | null {
  const origin = request.headers.get('origin');
  if (origin !== null) {
    if (!allowedOrigins(request).has(origin.toLowerCase())) {
      return NextResponse.json(mappedError('rejected', 'cross-origin request'), { status: 403 });
    }
  } else {
    const site = request.headers.get('sec-fetch-site');
    if (site !== null && site !== 'same-origin' && site !== 'none') {
      return NextResponse.json(mappedError('rejected', 'cross-origin request'), { status: 403 });
    }
  }

  if (options.json) {
    const mediaType = (request.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase();
    if (mediaType !== 'application/json') {
      return NextResponse.json(mappedError('validation_error', 'Content-Type must be application/json'), {
        status: 415,
      });
    }
  }
  return null;
}

function allowedOrigins(request: NextRequest): Set<string> {
  const allowed = new Set<string>();
  const configured = originOf(resolveAppBaseUrl());
  if (configured) allowed.add(configured);

  // A proxy chain appends, so the client-facing value is the first entry
  // (same reading as session.ts's `isSecureRequest`).
  const forwardedProto = firstEntry(request.headers.get('x-forwarded-proto'));
  const forwardedHost = firstEntry(request.headers.get('x-forwarded-host'));
  const proto = forwardedProto || request.nextUrl.protocol.replace(/:$/, '');
  const host = forwardedHost || request.headers.get('host') || request.nextUrl.host;
  const own = originOf(`${proto}://${host}`);
  if (own) allowed.add(own);
  return allowed;
}

function firstEntry(value: string | null): string {
  return (value ?? '').split(',')[0].trim();
}

function originOf(url: string): string | null {
  try {
    return new URL(url).origin.toLowerCase();
  } catch {
    return null;
  }
}
