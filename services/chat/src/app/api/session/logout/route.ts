import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { clearSessionCookie, getSessionCredential } from '@/lib/session';
import { gatewayLogout } from '@/lib/weave-api-server';

/**
 * POST /api/session/logout — deletes this app's own httpOnly session
 * cookie, and, for an SSO-derived (`kind: 'session'`) credential, ALSO
 * calls Weave-API's own POST /v1/auth/logout first so that browser's
 * session ends there too (task requirement — a Personal-API-Token has no
 * such server-side session to end: personal API tokens are not
 * per-session, so "logging out" of this UI for THAT credential kind only
 * ever forgets the cookie here; revoking the token itself happens on
 * Weave-API's own side, via its user/token administration — see that
 * repo's CLI). `gatewayLogout` is best-effort and never throws (see its
 * own docstring), so this app's own cookie is always cleared regardless
 * of whether the gateway call succeeded.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const credential = getSessionCredential(request);
  if (credential?.kind === 'session') {
    await gatewayLogout(credential.value);
  }

  const response = NextResponse.json({ ok: true });
  clearSessionCookie(request, response);
  return response;
}
