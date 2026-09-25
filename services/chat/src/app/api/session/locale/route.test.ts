import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { PUT } from '@/app/api/session/locale/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const TOKEN = 'wt_super_secret_personal_token';

function localeRequest({
  withCookie = true,
  body = { locale: 'en' },
}: { withCookie?: boolean; body?: unknown } = {}): NextRequest {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=${TOKEN}`;
  return new NextRequest('http://localhost/api/session/locale', {
    method: 'PUT',
    headers,
    body: JSON.stringify(body),
  });
}

describe('PUT /api/session/locale', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('answers 401 without calling fetch when the session cookie is missing', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await PUT(localeRequest({ withCookie: false }));

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('answers 400 without calling fetch for an invalid locale', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await PUT(localeRequest({ body: { locale: 'fr' } }));

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('PUTs the locale to Weave-API and relays the result', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(String(input)).toBe('http://localhost:8004/v1/me/locale');
      expect(init?.method).toBe('PUT');
      expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
      expect(JSON.parse(init?.body as string)).toEqual({ locale: 'en' });
      return new Response(JSON.stringify({ locale: 'en' }), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await PUT(localeRequest({ body: { locale: 'en' } }));

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ locale: 'en' });
  });

  it('passes through Weave-API\'s 503 (account store unreachable) instead of squashing it to 502', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'account store unreachable' }), { status: 503 })),
    );

    const res = await PUT(localeRequest());

    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.kind).toBe('gateway_unreachable');
  });

  it('maps an expired/invalid token (401 from Weave-API) to 401 auth_expired', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Not authenticated' }), { status: 401 })));

    const res = await PUT(localeRequest());

    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.kind).toBe('auth_expired');
  });
});
