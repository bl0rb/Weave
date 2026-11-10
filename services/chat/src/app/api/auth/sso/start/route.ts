import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { buildSsoLoginUrl, buildWeaveLoginUrl, isSsoLoginEnabled, isWeaveLoginEnabled } from '@/lib/sso';
import { codeChallengeFor, createCodeVerifier, setPkceCookie } from '@/lib/sso-pkce';

// A fresh random verifier and cookie on every request — never cacheable.
export const dynamic = 'force-dynamic';

/**
 * GET /api/auth/sso/start?provider=oidc|ingest — step 0 of the handoff
 * /api/auth/sso/callback describes: mints a PKCE verifier, keeps it in this
 * browser's own short-lived cookie (sso-pkce.ts), and redirects to
 * Weave-API's login with the matching `code_challenge`, so the code that
 * comes back can only be redeemed by this same browser.
 *
 * A GET a cross-site page can trigger on purpose: all it can do is replace
 * the victim's verifier with a new one it never sees, which fails an
 * in-flight login at worst. Planting a verifier cookie this way and then
 * sending the victim to the callback with the attacker's own code doesn't
 * work either: a code bound to the attacker's challenge needs the attacker's
 * verifier, and Weave-API refuses an unbound code (a login started without
 * a challenge) whenever a verifier comes with it. A provider this deployment hasn't enabled is a
 * 404, the same answer Weave-API gives for an unconfigured login.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const provider = request.nextUrl.searchParams.get('provider');
  const verifier = createCodeVerifier();
  const challenge = codeChallengeFor(verifier);

  let target: string;
  if (provider === 'ingest' && isWeaveLoginEnabled()) {
    target = buildWeaveLoginUrl(challenge);
  } else if (provider === 'oidc' && isSsoLoginEnabled()) {
    target = buildSsoLoginUrl(challenge);
  } else {
    return NextResponse.json({ detail: 'Not Found' }, { status: 404 });
  }

  const response = NextResponse.redirect(target);
  setPkceCookie(request, response, verifier);
  return response;
}
