import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { SESSION_COOKIE_NAME } from '@/lib/session';

/**
 * Redirects to /login when the session cookie is simply absent. This is a
 * cheap, presence-only check — it does NOT validate the token (that would
 * mean an extra Weave-API round trip on every navigation). An expired or
 * revoked-but-still-present token still reaches the page; the first
 * authenticated call it makes (GET /api/bots on mount) gets a 401 from
 * Weave-API and the chat UI itself sends the user back to /login from
 * there — see components/chat/chat-app.tsx.
 */
export function middleware(request: NextRequest): NextResponse {
  const hasSession = request.cookies.has(SESSION_COOKIE_NAME);
  if (!hasSession) {
    const loginUrl = new URL('/login', request.url);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  // Only the chat page itself needs the redirect; /login must stay
  // reachable without a cookie (else no one could ever log in), and the
  // API routes each enforce their own session check and answer JSON, not
  // a redirect, on a missing cookie.
  matcher: ['/'],
};
