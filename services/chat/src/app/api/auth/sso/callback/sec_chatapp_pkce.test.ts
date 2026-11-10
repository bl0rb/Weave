import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '@/app/api/auth/sso/callback/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';
import { SSO_PKCE_COOKIE_NAME } from '@/lib/sso-pkce';

const VERIFIER = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk';

function callbackRequest(cookie?: string): NextRequest {
  const headers: Record<string, string> = cookie ? { cookie } : {};
  return new NextRequest('http://localhost/api/auth/sso/callback?code=the-one-time-code', { headers });
}

function acceptingExchange() {
  const expiresAt = new Date(Date.now() + 3600_000).toISOString();
  return vi.fn(async () => new Response(JSON.stringify({ session_token: 'tok', expires_at: expiresAt }), { status: 200 }));
}

describe('GET /api/auth/sso/callback — PKCE binding (login CSRF)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('never redeems a code in a browser that did not start the login (no verifier cookie)', async () => {
    const fetchMock = acceptingExchange();
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(callbackRequest());

    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.headers.get('location')).toBe('http://localhost:3000/login?error=invalid_code');
    expect(res.headers.get('set-cookie') ?? '').not.toContain(SESSION_COOKIE_NAME);
  });

  it('rejects a verifier cookie that is not shaped like one this app mints', async () => {
    const fetchMock = acceptingExchange();
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(callbackRequest(`${SSO_PKCE_COOKIE_NAME}=short`));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.headers.get('location')).toBe('http://localhost:3000/login?error=invalid_code');
    expect(res.headers.get('set-cookie') ?? '').toMatch(new RegExp(`${SSO_PKCE_COOKIE_NAME}=;.*Max-Age=0`));
  });

  it('sends the verifier as code_verifier and deletes the cookie on success', async () => {
    const fetchMock = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      expect(JSON.parse(init!.body as string)).toEqual({ code: 'the-one-time-code', code_verifier: VERIFIER });
      return acceptingExchange()();
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(callbackRequest(`${SSO_PKCE_COOKIE_NAME}=${VERIFIER}`));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.headers.get('location')).toBe('http://localhost:3000/');
    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toContain(`${SESSION_COOKIE_NAME}=tok`);
    expect(setCookie).toMatch(new RegExp(`${SSO_PKCE_COOKIE_NAME}=;[^,]*Path=/api/auth/sso[^,]*Max-Age=0|${SSO_PKCE_COOKIE_NAME}=;[^,]*Max-Age=0[^,]*Path=/api/auth/sso`));
  });

  it('deletes the cookie when Weave-API rejects the code (e.g. a wrong verifier)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Invalid or expired code"}', { status: 400 })));

    const res = await GET(callbackRequest(`${SSO_PKCE_COOKIE_NAME}=${VERIFIER}`));

    expect(res.headers.get('location')).toBe('http://localhost:3000/login?error=invalid_code');
    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toMatch(new RegExp(`${SSO_PKCE_COOKIE_NAME}=;`));
    expect(setCookie).not.toContain(SESSION_COOKIE_NAME);
  });
});
