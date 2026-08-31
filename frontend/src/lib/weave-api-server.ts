import 'server-only';

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
 * Handler under src/app/api/**), which reads the token off that cookie,
 * attaches it as `Authorization: Bearer <token>` to a same-process
 * server-to-server call to Weave-API, and relays back only the response
 * body Weave-API sent (or, for the SSE stream, the response bytes
 * themselves, untouched) — never the header it used to get there. From
 * the browser's point of view every request is same-origin, so there is
 * no CORS to configure and no code path in the client bundle that could
 * even reference the token if it wanted to. The one-time cost is this
 * thin proxy layer; the payoff is that "did the token leak to the
 * browser" stops being a question that depends on every future line of
 * client code getting it right.
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
 * The one place an actual HTTP call to Weave-API is made. Returns the raw
 * `Response` on ANY completed HTTP exchange (2xx through 5xx) — callers
 * decide what a given status means for their own route (see
 * `weaveApiFetchJson` below for the common case, and the `/chat/stream`
 * Route Handler for why streaming needs the raw response instead).
 * Only a transport-level failure (no response at all) becomes a thrown
 * `GatewayError('unreachable', ...)` here.
 */
export async function weaveApiFetch(path: string, token: string, init?: RequestInit): Promise<Response> {
  const url = `${resolveWeaveApiBaseUrl()}${path}`;
  try {
    return await fetch(url, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        Authorization: `Bearer ${token}`,
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
export async function weaveApiFetchJson<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await weaveApiFetch(path, token, init);

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
