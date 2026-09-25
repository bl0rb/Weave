import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '@/app/api/session/me/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const TOKEN = 'wt_super_secret_personal_token';

function meRequest({ withCookie = true }: { withCookie?: boolean } = {}): NextRequest {
  const headers: Record<string, string> = {};
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=${TOKEN}`;
  return new NextRequest('http://localhost/api/session/me', { headers });
}

describe('GET /api/session/me', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('answers 401 without calling fetch when the session cookie is missing', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(meRequest({ withCookie: false }));

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('relays the account summary on success, using the cookie token as the Bearer header', async () => {
    const me = { username: 'ada', locale: 'en' };
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(String(input)).toBe('http://localhost:8004/v1/me');
      expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
      return new Response(JSON.stringify(me), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(meRequest());

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(me);
  });

  it('relays a null account locale unchanged', async () => {
    const me = { username: 'ada', locale: null };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(me), { status: 200 })));

    const res = await GET(meRequest());

    expect(await res.json()).toEqual(me);
  });

  it('maps an expired/invalid token (401 from Weave-API) to 401 auth_expired', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Not authenticated' }), { status: 401 })));

    const res = await GET(meRequest());

    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.kind).toBe('auth_expired');
  });

  it('maps Weave-API being unreachable (502 from Weave-API itself) to gateway_unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'unreachable' }), { status: 502 })));

    const res = await GET(meRequest());

    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.kind).toBe('gateway_unreachable');
  });
});
