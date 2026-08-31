import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '@/app/api/bots/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const TOKEN = 'wt_super_secret_personal_token';

function botsRequest({ withCookie = true }: { withCookie?: boolean } = {}): NextRequest {
  const headers: Record<string, string> = {};
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=${TOKEN}`;
  return new NextRequest('http://localhost/api/bots', { headers });
}

describe('GET /api/bots', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('answers 401 without calling fetch when the session cookie is missing', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(botsRequest({ withCookie: false }));

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('relays the bot list on success, using the cookie token as the Bearer header', async () => {
    const bots = [{ id: 'legal-support', name: 'Legal', description: null, retrieval: { enabled: true } }];
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(String(input)).toBe('http://localhost:8004/v1/bots');
      expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
      return new Response(JSON.stringify(bots), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(botsRequest());

    expect(res.status).toBe(200);
    const rawText = await res.clone().text();
    expect(rawText).not.toContain(TOKEN);
    expect(await res.json()).toEqual(bots);
  });

  it('maps an expired/invalid token (401 from Weave-API) to 401 auth_expired', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Not authenticated' }), { status: 401 })));

    const res = await GET(botsRequest());

    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.kind).toBe('auth_expired');
  });

  it('maps Weave-API being unreachable (502 from Weave-API itself) to gateway_unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Weave-Runtime unreachable' }), { status: 502 })));

    const res = await GET(botsRequest());

    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.kind).toBe('gateway_unreachable');
  });
});
