import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '@/app/api/auth/sso/callback/route';
import { SESSION_COOKIE_NAME, SESSION_KIND_COOKIE_NAME } from '@/lib/session';

function callbackRequest(query = ''): NextRequest {
  return new NextRequest(`http://localhost/api/auth/sso/callback${query}`);
}

describe('GET /api/auth/sso/callback', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it('redirects to /login?error=missing_code without ever calling fetch when there is no code', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(callbackRequest());

    expect(res.status).toBe(307);
    expect(res.headers.get('location')).toBe('http://localhost:3000/login?error=missing_code');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it(
    'on a successful exchange: calls POST /v1/auth/session/exchange SERVER-SIDE with the code, ' +
      'sets the session-kind httpOnly cookie, redirects to /, and the raw token never appears anywhere in the response',
    async () => {
      const TOKEN = 'sso-session-token-xyz';
      const expiresAt = new Date(Date.now() + 3600_000).toISOString();

      const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        expect(String(url)).toBe('http://localhost:8004/v1/auth/session/exchange');
        expect(init?.method).toBe('POST');
        expect(JSON.parse(init!.body as string)).toEqual({ code: 'the-one-time-code' });
        return new Response(JSON.stringify({ session_token: TOKEN, expires_at: expiresAt }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      });
      vi.stubGlobal('fetch', fetchMock);

      const res = await GET(callbackRequest('?code=the-one-time-code'));

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(res.status).toBe(307);
      expect(res.headers.get('location')).toBe('http://localhost:3000/');

      const setCookie = res.headers.get('set-cookie') ?? '';
      expect(setCookie).toContain(`${SESSION_COOKIE_NAME}=${TOKEN}`);
      expect(setCookie).toContain(`${SESSION_KIND_COOKIE_NAME}=session`);
      expect(setCookie).toMatch(/HttpOnly/i);

      // The token must not be observable anywhere else in what the browser
      // gets back — no JSON body, no other header, no query string. Only
      // `set-cookie` (the real cookie) and Next's own internal
      // `x-middleware-set-cookie` (its middleware-forwarding mirror of the
      // very same Set-Cookie headers, stripped before this response ever
      // reaches a real browser) legitimately carry it.
      const rawBody = await res.clone().text();
      expect(rawBody).not.toContain(TOKEN);
      for (const [name, value] of res.headers.entries()) {
        if (name.toLowerCase() === 'set-cookie' || name.toLowerCase() === 'x-middleware-set-cookie') continue;
        expect(value).not.toContain(TOKEN);
      }
      expect(res.headers.get('location')).not.toContain(TOKEN);
    }
  );

  it('redirects to /login?error=invalid_code when Weave-API rejects the code (unknown/expired/already used)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'invalid or expired code' }), { status: 400 }))
    );

    const res = await GET(callbackRequest('?code=bad-code'));

    expect(res.headers.get('location')).toBe('http://localhost:3000/login?error=invalid_code');
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it('redirects to /login?error=gateway_unreachable when Weave-API cannot be reached at all', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed: ECONNREFUSED');
      })
    );

    const res = await GET(callbackRequest('?code=some-code'));

    expect(res.headers.get('location')).toBe('http://localhost:3000/login?error=gateway_unreachable');
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it('redirects to /login?error=invalid_code on a malformed (non-JSON / missing session_token) 200 response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('not json', { status: 200 })));

    const res = await GET(callbackRequest('?code=some-code'));

    expect(res.headers.get('location')).toBe('http://localhost:3000/login?error=invalid_code');
  });

  it('redirects to the configured public origin, never to the internal host behind the proxy', async () => {
    // Behind the ingress `request.url` is http://localhost:3000 — sending the
    // browser there produced ERR_CONNECTION_REFUSED after the SSO handoff.
    vi.stubEnv('CHAT_APP_BASE_URL', 'https://chat.example.com');
    const expiresAt = new Date(Date.now() + 3600_000).toISOString();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ session_token: 'token', expires_at: expiresAt }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
      )
    );

    const res = await GET(callbackRequest('?code=the-one-time-code'));

    expect(res.headers.get('location')).toBe('https://chat.example.com/');
  });
});
