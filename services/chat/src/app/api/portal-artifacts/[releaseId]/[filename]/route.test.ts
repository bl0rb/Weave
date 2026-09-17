import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const TOKEN = 'wt_super_secret_personal_token';

function artifactRequest({ withCookie = true }: { withCookie?: boolean } = {}): NextRequest {
  const headers: Record<string, string> = {};
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=${TOKEN}`;
  return new NextRequest('http://localhost/api/portal-artifacts/rel-1/diagram.png', { headers });
}

function params(releaseId = 'rel-1', filename = 'diagram.png') {
  return { params: Promise.resolve({ releaseId, filename }) };
}

describe('GET /api/portal-artifacts/[releaseId]/[filename]', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('answers 401 without calling fetch when the session cookie is missing', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(artifactRequest({ withCookie: false }), params());

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('proxies the byte content and content-type through Weave-API, only ever hitting the one built path', async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(String(input)).toBe('http://localhost:8004/v1/portal/releases/rel-1/artifacts/diagram.png');
      expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
      return new Response(bytes, { status: 200, headers: { 'content-type': 'image/png' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(artifactRequest(), params());

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('image/png');
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(bytes);
  });

  it('never forwards a caller-controlled path — releaseId/filename are always encoded into the fixed Weave-API path', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      expect(String(input)).toBe('http://localhost:8004/v1/portal/releases/rel%2F1/artifacts/evil.png');
      return new Response(new Uint8Array(), { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(artifactRequest(), params('rel/1', 'evil.png'));
    expect(res.status).toBe(404);
  });

  it('maps a 404 from Weave-API to 404', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Artifact not found' }), { status: 404 })));

    const res = await GET(artifactRequest(), params());
    expect(res.status).toBe(404);
  });
});
