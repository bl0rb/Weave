import 'server-only';
import { resolveWeaveApiBaseUrl } from '@/lib/weave-api-server';

/**
 * Whether the "Mit SSO anmelden" button on /login should appear at all.
 *
 * Weave-API has no runtime-queryable "is OIDC configured" surface: its own
 * `/v1/auth/oidc/*` router (backend/app/api/auth.py) is gated by
 * `require_oidc_enabled` at the ROUTER level, so an unconfigured gateway
 * makes every one of those endpoints — including the login endpoint this
 * button would link to — 404 exactly as if the router had never been
 * mounted (see that module's own docstring: "an unconfigured deployment
 * must look exactly like this router was never mounted at all"). That is
 * deliberate on Weave-API's side and this app must not work around it by
 * probing GET /v1/auth/oidc/login itself (a real request there fetches the
 * provider's OWN discovery document and sets a state cookie — a live
 * side-effecting call to an external IdP, not a cheap feature check, and
 * exactly the kind of probe that gating exists to discourage). There is
 * also no separate `/v1/config`-style endpoint anywhere in that service
 * (checked backend/app/main.py's router list) that exposes this as a
 * plain boolean.
 *
 * So this is this app's OWN setting instead, read live per request (never
 * baked into the client bundle — see next.config.ts's own reasoning on
 * `WEAVE_API_BASE_URL` for why nothing here uses `NEXT_PUBLIC_*` or
 * next.config.ts's `env` block). Whoever deploys this UI alongside
 * Weave-API is responsible for keeping the two in sync: set this to
 * `true` if and only if that Weave-API deployment's own `OIDC_ISSUER` and
 * `OIDC_CLIENT_ID` (backend/app/core/config.py) are both set. A mismatch
 * fails safe in the user-facing direction that matters most for THIS
 * button specifically: if this is `true` but the gateway is actually
 * unconfigured, the button still appears but a click 404s at Weave-API
 * before anything sensitive happens (no state cookie survives that,
 * nothing is provisioned) — annoying, not unsafe. The other direction
 * (`false` while the gateway IS configured) just hides a working feature.
 * Neither direction can turn into an open redirect or a credential leak,
 * since Weave-API's own `return_to` allowlist check
 * (`OIDC_POST_LOGIN_ALLOWED_URLS`) is the actual security boundary here,
 * not this flag.
 */
export function isSsoLoginEnabled(): boolean {
  const raw = process.env.WEAVE_API_OIDC_ENABLED?.trim().toLowerCase();
  return raw === 'true' || raw === '1';
}

/**
 * This UI's OWN externally-reachable origin — deliberately a SEPARATE
 * setting from `WEAVE_API_BASE_URL`. That variable names Weave-API's
 * origin for THIS SERVER'S outgoing calls and may well be an
 * internal-only address (a Docker service name, a cluster-internal host)
 * that a real end-user browser could never reach. `return_to` below, by
 * contrast, is a value Weave-API hands straight back to the BROWSER as a
 * redirect target (see /api/auth/sso/callback's own docstring), so it
 * must be this app's public origin, and it must match one entry of
 * Weave-API's own `OIDC_POST_LOGIN_ALLOWED_URLS` EXACTLY (scheme/host/port), so it is
 * never derived from a request's own `Host` header (trivially spoofable,
 * and would make this app's own return_to move around per-request instead
 * of being the one fixed value ops actually put in that allowlist).
 * Falls back to the same local-dev default this app's own server listens
 * on when unset, matching how every other Weave frontend's own base-URL
 * setting defaults for local development.
 */
function resolveAppBaseUrl(): string {
  const configured = process.env.APP_BASE_URL?.trim();
  const base = configured && configured.length > 0 ? configured : 'http://localhost:3000';
  return base.replace(/\/+$/, '');
}

/** The exact path Weave-API's own callback redirects the browser back to
 * with `?code=<code>` appended (see /api/auth/sso/callback's own route
 * file, which this path must match). */
export const SSO_CALLBACK_PATH = '/api/auth/sso/callback';

/**
 * `WEAVE_API_PUBLIC_BASE_URL` names Weave-API's own BROWSER-reachable
 * origin — the address the "Mit SSO anmelden" link itself navigates to.
 * Deliberately separate from `WEAVE_API_BASE_URL` (this server's own,
 * possibly-internal-only address for its server-to-server proxy calls,
 * see weave-api-server.ts) for the same reason `resolveAppBaseUrl` above
 * is separate from a request's `Host` header: a browser redirect target
 * and a server-to-server call target are not guaranteed to be the same
 * address at all once there is any reverse proxy, service mesh, or
 * internal DNS between them. Falls back to `WEAVE_API_BASE_URL` for the
 * common case (local dev, or any deployment where the two really do
 * coincide) so a deployment that never sets this separately keeps
 * working.
 */
function resolveWeaveApiPublicBaseUrl(): string {
  const configured = process.env.WEAVE_API_PUBLIC_BASE_URL?.trim();
  const base = configured && configured.length > 0 ? configured : resolveWeaveApiBaseUrl();
  return base.replace(/\/+$/, '');
}

/**
 * The full URL the "Mit SSO anmelden" link on /login points at:
 * Weave-API's own GET /v1/auth/oidc/login, carrying `return_to` set to
 * this app's own callback route — see that route's docstring and
 * Weave-API backend/app/api/auth.py's "Cross-origin post-login handoff"
 * section for the full contract this starts. Only meaningful to call when
 * `isSsoLoginEnabled()` is true; does not itself check that.
 */
export function buildSsoLoginUrl(): string {
  const returnTo = `${resolveAppBaseUrl()}${SSO_CALLBACK_PATH}`;
  const query = new URLSearchParams({ return_to: returnTo });
  return `${resolveWeaveApiPublicBaseUrl()}/v1/auth/oidc/login?${query.toString()}`;
}


/**
 * Whether the "Mit Weave anmelden" button appears — the federated login
 * that sends people to Weave-Ingest's own sign-in page (Weave-API's
 * `/v1/auth/ingest/login`).
 *
 * This is the login an operator should normally enable: Weave-Ingest
 * already administers local users, teams and a table of OIDC connections,
 * so whoever can sign in there can sign in here, by whichever method, and
 * neither this app nor the gateway needs to know that any of those methods
 * exist. `isSsoLoginEnabled` above stays for a deployment that points the
 * gateway straight at one OIDC provider instead; the two are alternatives,
 * not a pair.
 *
 * Same shape and same reasoning as that flag: this app's OWN setting, read
 * live per request, kept in sync by whoever deploys the two together (set
 * it if and only if Weave-API's own `INGEST_LOGIN_URL` and
 * `INGEST_API_URL` are set). A mismatch fails the same harmless way — the
 * button appears and a click 404s at the gateway before anything happens.
 */
export function isWeaveLoginEnabled(): boolean {
  const raw = process.env.WEAVE_API_INGEST_LOGIN_ENABLED?.trim().toLowerCase();
  return raw === 'true' || raw === '1';
}

/**
 * The URL that button points at: Weave-API's GET /v1/auth/ingest/login,
 * carrying the same `return_to` contract `buildSsoLoginUrl` uses — both
 * flows converge on this app's one callback route, because both end in a
 * one-time code this app's own backend exchanges for a session.
 */
export function buildWeaveLoginUrl(): string {
  const returnTo = `${resolveAppBaseUrl()}${SSO_CALLBACK_PATH}`;
  const query = new URLSearchParams({ return_to: returnTo });
  return `${resolveWeaveApiPublicBaseUrl()}/v1/auth/ingest/login?${query.toString()}`;
}
