import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { POST } from '@/app/api/session/login/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

function loginRequest(body: unknown, init: { url?: string; headers?: Record<string, string> } = {}): NextRequest {
  return new NextRequest(init.url ?? 'http://localhost/api/session/login', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(init.headers ?? {}) },
    body: JSON.stringify(body),
  });
}

/** A Weave-API that accepts any token — for the cookie-attribute tests
 * below, which care about the response's Set-Cookie, not the exchange. */
function stubAcceptingWeaveApi(): void {
  vi.stubGlobal('fetch', async () => new Response('[]', {
    status: 200,
    headers: { 'content-type': 'application/json' },
  }));
}

describe('POST /api/session/login', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('rejects an empty token without ever calling fetch', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(loginRequest({ token: '   ' }));

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it('on a token Weave-API accepts, sets an httpOnly session cookie and never echoes the token in the response body', async () => {
    const TOKEN = 'wt_super_secret_personal_token';
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(String(input)).toBe('http://localhost:8004/v1/bots');
      expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
      return new Response(JSON.stringify([{ id: 'legal-support', name: 'Legal', description: null, retrieval: { enabled: true } }]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(loginRequest({ token: TOKEN }));

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const setCookie = res.headers.get('set-cookie');
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain(`${SESSION_COOKIE_NAME}=${TOKEN}`);
    expect(setCookie).toMatch(/HttpOnly/i);

    const rawBody = await res.clone().text();
    expect(rawBody).not.toContain(TOKEN);
    const body = await res.json();
    expect(body).toEqual({ ok: true });
  });

  // The cookie is useless if the browser refuses to store it: Safari drops
  // a `Secure` cookie that arrives over plain http even on localhost, which
  // turns a 200 here into a login that silently never happens. NODE_ENV is
  // 'production' inside the container regardless of the scheme, so the flag
  // has to follow the request — see isSecureRequest in @/lib/session.
  it('over plain http, sets the session cookie WITHOUT Secure', async () => {
    stubAcceptingWeaveApi();

    const res = await POST(loginRequest({ token: 'a-token' }));

    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).not.toMatch(/Secure/i);
  });

  it('behind a TLS-terminating proxy (x-forwarded-proto: https), sets it WITH Secure', async () => {
    stubAcceptingWeaveApi();

    const res = await POST(
      loginRequest({ token: 'a-token' }, { headers: { 'x-forwarded-proto': 'https' } })
    );

    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).toMatch(/Secure/i);
  });

  it('reads only the ORIGINAL scheme out of a proxy chain', async () => {
    stubAcceptingWeaveApi();

    const res = await POST(
      loginRequest({ token: 'a-token' }, { headers: { 'x-forwarded-proto': 'https,http' } })
    );

    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).toMatch(/Secure/i);
  });

  it('on an invalid token (401 from Weave-API), answers 401 and sets no cookie', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'Not authenticated' }), { status: 401 }))
    );

    const res = await POST(loginRequest({ token: 'not-a-real-token' }));

    expect(res.status).toBe(401);
    expect(res.headers.get('set-cookie')).toBeNull();
    const body = await res.json();
    expect(body.kind).toBe('invalid_token');
  });

  it('when Weave-API is unreachable, answers 502 and sets no cookie — never a raw network error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed: ECONNREFUSED');
      })
    );

    const res = await POST(loginRequest({ token: 'anything' }));

    expect(res.status).toBe(502);
    expect(res.headers.get('set-cookie')).toBeNull();
    const body = await res.json();
    expect(body.kind).toBe('gateway_unreachable');
    expect(body.message).not.toMatch(/ECONNREFUSED/);
  });
});
