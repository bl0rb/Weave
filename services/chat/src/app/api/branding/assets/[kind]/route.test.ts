import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox";
const SVG = new TextEncoder().encode('<svg xmlns="http://www.w3.org/2000/svg"/>');

function assetRequest(kind: string, headers: Record<string, string> = {}): [NextRequest, { params: Promise<{ kind: string }> }] {
  return [new NextRequest(`http://localhost/api/branding/assets/${kind}?v=0123456789abcdef`, { headers }), { params: Promise.resolve({ kind }) }];
}

function upstream(status: number, headers: Record<string, string> = {}, body: BodyInit | null = status === 200 ? SVG : null) {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response(body, { status, headers }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('GET /api/branding/assets/[kind]', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('serves an SVG with the sandbox CSP, nosniff, inline disposition, public cache and the upstream ETag', async () => {
    const fetchMock = upstream(200, { 'content-type': 'image/svg+xml; charset=utf-8', etag: '"sha"', 'set-cookie': 'evil=1', 'x-upstream': 'leak' });

    const res = await GET(...assetRequest('logo'));

    expect(res.status).toBe(200);
    expect(String(fetchMock.mock.calls[0][0])).toBe('http://localhost:8004/v1/branding/assets/logo');
    expect(res.headers.get('content-type')).toBe('image/svg+xml');
    expect(res.headers.get('content-security-policy')).toBe(CSP);
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(res.headers.get('content-disposition')).toBe('inline; filename="branding-logo.svg"');
    expect(res.headers.get('cache-control')).toBe('public, max-age=300');
    expect(res.headers.get('etag')).toBe('"sha"');
    expect(res.headers.get('set-cookie')).toBeNull();
    expect(res.headers.get('x-upstream')).toBeNull();
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(SVG);
  });

  it.each([
    ['image/gif', 'busy', 'branding-busy.gif'],
    ['image/png', 'logo', 'branding-logo.png'],
  ])('names a %s asset by kind and type', async (type, kind, filename) => {
    upstream(200, { 'content-type': type }, new Uint8Array([1, 2, 3]));

    const res = await GET(...assetRequest(kind));

    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe(type);
    expect(res.headers.get('content-disposition')).toBe(`inline; filename="${filename}"`);
  });

  it.each(['icon', 'LOGO', '..', 'logo.svg'])('answers 404 for kind %j without calling Weave-API', async (kind) => {
    const fetchMock = upstream(200, { 'content-type': 'image/png' });

    const res = await GET(...assetRequest(kind));

    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('forwards If-None-Match and passes a 304 through without a body, and never sends a credential', async () => {
    const fetchMock = upstream(304, { etag: '"sha"' });

    const res = await GET(...assetRequest('busy', { 'if-none-match': '"sha"', cookie: `${SESSION_COOKIE_NAME}=wt_token` }));

    expect(res.status).toBe(304);
    expect(res.headers.get('etag')).toBe('"sha"');
    // Same 304 header set as Weave-API's.
    expect(res.headers.get('content-security-policy')).toBe(CSP);
    expect(await res.text()).toBe('');
    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers).toEqual({ 'If-None-Match': '"sha"' });
  });

  it('passes a 404 through (asset not set)', async () => {
    upstream(404, { 'content-type': 'application/json' }, '{"detail":"not found"}');

    expect((await GET(...assetRequest('logo'))).status).toBe(404);
  });

  it.each(['text/html', 'image/svg', 'application/octet-stream', ''])('answers 502 for an upstream content type %j', async (type) => {
    upstream(200, type ? { 'content-type': type } : {}, new Uint8Array([1]));

    const res = await GET(...assetRequest('logo'));

    expect(res.status).toBe(502);
    expect(res.headers.get('content-type')).toContain('application/json');
  });

  it('answers 502 for a body larger than 1 MiB', async () => {
    upstream(200, { 'content-type': 'image/png' }, new Uint8Array(1024 * 1024 + 1));

    expect((await GET(...assetRequest('logo'))).status).toBe(502);
  });

  it('answers 502 for an upstream server error or an unreachable Weave-API', async () => {
    upstream(500);
    expect((await GET(...assetRequest('logo'))).status).toBe(502);

    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));
    expect((await GET(...assetRequest('logo'))).status).toBe(502);
  });
});
