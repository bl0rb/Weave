import 'server-only';
import type { SessionCredential } from '@/lib/session';

/**
 * The ONLY module in this app allowed to hold the Weave-API bearer token
 * in memory long enough to attach it to a request, and the only one that
 * talks to Weave-API's `/v1/*` surface directly.
 *
 * WHY THE TOKEN NEVER TOUCHES THE BROWSER (see session.ts for the cookie
 * half of the same decision):
 *
 * Weave-API's personal API tokens are long-lived bearer credentials —
 * whoever holds one can act as that user against every `/v1/*` route
 * (see Weave-API backend/app/core/auth.py). A token that a browser tab
 * ever holds in JavaScript-reachable form (a JS variable, localStorage,
 * a non-httpOnly cookie, a query string, ...) is a token exposed to
 * every XSS bug this app or any of its dependencies (react-markdown
 * rendering a bot's answer, any future third-party script) will ever
 * have, for as long as that token remains valid — which, unlike a short
 * session cookie, can be weeks. It would also have to be sent
 * cross-origin to Weave-API's own host on every request, which means
 * CORS: Weave-API would need to allow this frontend's origin and this
 * frontend would need to keep the token somewhere JS can read it to set
 * the `Authorization` header itself — reintroducing the exact exposure
 * above just to make the network topology simpler.
 *
 * Instead: the token is set ONCE, server-side, as an httpOnly cookie
 * (src/lib/session.ts) that JavaScript on this origin can never read.
 * Every UI action goes to this SAME Next.js server first (a Route
 * Handler under src/app/api/**), which reads the credential off that
 * cookie and attaches it to a same-process server-to-server call to
 * Weave-API, relaying back only the response body Weave-API sent (or, for
 * the SSE stream, the response bytes themselves, untouched) — never the
 * header it used to get there. From the browser's point of view every
 * request is same-origin, so there is no CORS to configure and no code
 * path in the client bundle that could even reference the credential if
 * it wanted to. The one-time cost is this thin proxy layer; the payoff is
 * that "did the credential leak to the browser" stops being a question
 * that depends on every future line of client code getting it right.
 *
 * TWO CREDENTIAL KINDS, ONE HEADER-CHOOSING PLACE (`credentialHeaders`
 * below): a Personal-API-Token (session.ts's `kind: 'bearer'`) is a
 * Weave-API `ApiToken` and must go out as `Authorization: Bearer <token>`
 * (Weave-API backend/app/core/auth.py's `get_current_user` tries that
 * header first). An SSO-derived session token (`kind: 'session'`, minted
 * by POST /v1/auth/session/exchange after the cross-origin OIDC handoff —
 * see /api/auth/sso/callback's own docstring) is a Weave-API `Session`
 * row instead, which that same `get_current_user` only ever resolves from
 * its OWN cookie jar (`_authenticate_session_cookie`, keyed on
 * `weave_api_session` — see `GATEWAY_SESSION_COOKIE_NAME` below), never
 * from an `Authorization` header. Sending an SSO session token as a
 * Bearer credential (or a Personal-API-Token as a `Cookie` header) would
 * both just 401 — Weave-API never accepts either credential via the
 * other's transport. Every call in this module funnels through
 * `credentialHeaders` so this choice is made in exactly ONE place rather
 * than re-decided at each call site.
 */

export type GatewayErrorKind = 'unreachable' | 'unauthorized' | 'rejected';

/** Thrown by every function below instead of letting a raw fetch/HTTP
 * failure escape — callers (the Route Handlers) turn this into the right
 * HTTP response using src/lib/errors.ts, never into a leaked stack trace. */
export class GatewayError extends Error {
  readonly kind: GatewayErrorKind;
  readonly status: number | null;
  readonly detail: string | null;

  constructor(kind: GatewayErrorKind, status: number | null, detail: string | null) {
    super(`Weave-API ${kind}${status ? ` (HTTP ${status})` : ''}${detail ? `: ${detail}` : ''}`);
    this.name = 'GatewayError';
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}

export function resolveWeaveApiBaseUrl(): string {
  const configured = process.env.WEAVE_API_BASE_URL?.trim();
  const base = configured && configured.length > 0 ? configured : 'http://localhost:8004';
  return base.replace(/\/+$/, '');
}

// The exact cookie name Weave-API's own `get_current_user` reads a browser
// session from (backend/app/core/auth.py's `SESSION_COOKIE_NAME`) — taken
// over verbatim, not re-derived, since a mismatch here would silently 401
// every SSO-derived request. This is Weave-API's OWN cookie name on ITS
// origin; it has nothing to do with this app's own cookie
// (session.ts's `SESSION_COOKIE_NAME`, `weave_api_token`) beyond the
// coincidence that both services picked a similar name for their own,
// separate credential.
const GATEWAY_SESSION_COOKIE_NAME = 'weave_api_session';

/** The one place a `SessionCredential` (session.ts) becomes the header
 * Weave-API expects it back as — see this module's docstring for why the
 * two kinds cannot be sent interchangeably. */
function credentialHeaders(credential: SessionCredential): Record<string, string> {
  if (credential.kind === 'session') {
    return { Cookie: `${GATEWAY_SESSION_COOKIE_NAME}=${credential.value}` };
  }
  return { Authorization: `Bearer ${credential.value}` };
}

async function extractDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.clone().json();
    if (body && typeof body === 'object' && typeof (body as { detail?: unknown }).detail === 'string') {
      return (body as { detail: string }).detail;
    }
  } catch {
    // Non-JSON or empty body — no detail to extract.
  }
  return null;
}

/**
 * The one place an actual authenticated HTTP call to Weave-API is made.
 * Returns the raw `Response` on ANY completed HTTP exchange (2xx through
 * 5xx) — callers decide what a given status means for their own route
 * (see `weaveApiFetchJson` below for the common case, and the
 * `/chat/stream` Route Handler for why streaming needs the raw response
 * instead). Only a transport-level failure (no response at all) becomes a
 * thrown `GatewayError('unreachable', ...)` here.
 */
export async function weaveApiFetch(path: string, credential: SessionCredential, init?: RequestInit): Promise<Response> {
  const url = `${resolveWeaveApiBaseUrl()}${path}`;
  try {
    return await fetch(url, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        ...credentialHeaders(credential),
      },
      // This is live operator/user data, never a page Next.js should cache.
      cache: 'no-store',
    });
  } catch (cause) {
    throw new GatewayError('unreachable', null, cause instanceof Error ? cause.message : String(cause));
  }
}

/** `weaveApiFetch` plus classifying the status and decoding JSON — for
 * every Weave-API call except the SSE stream. Throws `GatewayError` for
 * anything other than 2xx so callers only need a single try/catch. */
export async function weaveApiFetchJson<T>(path: string, credential: SessionCredential, init?: RequestInit): Promise<T> {
  const response = await weaveApiFetch(path, credential, init);

  if (response.status === 401) {
    throw new GatewayError('unauthorized', 401, await extractDetail(response));
  }
  if (response.status >= 500) {
    throw new GatewayError('unreachable', response.status, await extractDetail(response));
  }
  if (!response.ok) {
    throw new GatewayError('rejected', response.status, await extractDetail(response));
  }

  return (await response.json()) as T;
}

// --- SSO cross-origin handoff: exchange + logout ----------------------------
//
// Both calls below deliberately do NOT go through weaveApiFetch/
// credentialHeaders above: neither carries a SessionCredential of this
// app's own in the normal sense.

export interface SessionExchangeResult {
  sessionToken: string;
  /** ISO-8601, exactly as Weave-API's own `SessionExchangeResponse.expires_at`
   * serializes it — passed straight to `setSessionCookie`'s
   * `maxAgeSeconds` by the callback route, never reparsed twice. */
  expiresAt: string;
}

/**
 * POST /v1/auth/session/exchange (Weave-API backend/app/api/auth.py) —
 * trades a cross-origin SSO handoff's one-time `code` for a real Weave-API
 * session token. Called ONLY from /api/auth/sso/callback, server-side,
 * exactly once per code (see that route's own docstring for the full
 * handoff contract). Carries no credential at all: the code itself is the
 * one-time, already-authenticated bearer of this call, exactly as
 * Weave-API's own docstring on that endpoint describes — there is no
 * Authorization header or cookie to attach here, unlike every other
 * function in this module.
 *
 * An unknown/expired/already-used code, a malformed response, and a
 * transport failure all reach the caller as a thrown `GatewayError` —
 * `kind: 'unreachable'` only for the transport-failure case (so the
 * callback route can tell "Weave-API said no" apart from "Weave-API could
 * not be reached" for its own German error message), `kind: 'rejected'`
 * for everything else. Never `'unauthorized'`: that kind is reserved for
 * an authenticated call's OWN credential being refused, which does not
 * apply here.
 */
export async function exchangeSessionCode(code: string): Promise<SessionExchangeResult> {
  const url = `${resolveWeaveApiBaseUrl()}/v1/auth/session/exchange`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
      cache: 'no-store',
    });
  } catch (cause) {
    throw new GatewayError('unreachable', null, cause instanceof Error ? cause.message : String(cause));
  }

  if (!response.ok) {
    // Weave-API deliberately never distinguishes unknown/expired/already-
    // used here (its own docstring's non-enumeration discipline) — neither
    // does this function; the callback route shows the same generic
    // German message for all of them.
    throw new GatewayError('rejected', response.status, await extractDetail(response));
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch (cause) {
    throw new GatewayError('rejected', response.status, cause instanceof Error ? cause.message : String(cause));
  }

  const sessionToken = (body as { session_token?: unknown } | null)?.session_token;
  const expiresAt = (body as { expires_at?: unknown } | null)?.expires_at;
  if (typeof sessionToken !== 'string' || !sessionToken || typeof expiresAt !== 'string' || !expiresAt) {
    throw new GatewayError('rejected', response.status, 'malformed session exchange response');
  }

  return { sessionToken, expiresAt };
}

/**
 * POST /v1/auth/logout (Weave-API backend/app/api/auth.py) — ends the
 * Weave-API-side Session row behind an SSO-derived session token, so
 * logging out of THIS UI also logs the browser out of the gateway itself
 * (task requirement), not just this app's own cookie. Deliberately
 * best-effort and NEVER throws: a merely-unreachable (or already-404,
 * e.g. OIDC was disabled after this session was created) gateway must
 * never block a user from logging out of this UI — see
 * /api/session/logout's own docstring for why clearing this app's own
 * cookie always happens regardless of this call's outcome. Only ever
 * called for a `kind: 'session'` credential — a Personal-API-Token has no
 * Weave-API Session row to end (see that route's own comment).
 */
export async function gatewayLogout(sessionToken: string): Promise<void> {
  const url = `${resolveWeaveApiBaseUrl()}/v1/auth/logout`;
  try {
    await fetch(url, {
      method: 'POST',
      headers: { Cookie: `${GATEWAY_SESSION_COOKIE_NAME}=${sessionToken}` },
      cache: 'no-store',
    });
  } catch {
    // Best-effort only — see docstring above.
  }
}
