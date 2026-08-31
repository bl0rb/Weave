import { NextResponse } from 'next/server';
import { clearSessionCookie } from '@/lib/session';

/** POST /api/session/logout — deletes the httpOnly session cookie. There is
 * nothing to tell Weave-API: personal API tokens are not per-session, so
 * "logging out" of this UI only forgets the cookie here, it does not
 * revoke the token itself (that happens on Weave-API's own side, via its
 * user/token administration — see that repo's CLI). */
export async function POST(): Promise<NextResponse> {
  const response = NextResponse.json({ ok: true });
  clearSessionCookie(response);
  return response;
}
