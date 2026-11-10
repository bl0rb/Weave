import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

function artifactRequest(): NextRequest {
  return new NextRequest('http://localhost/api/portal-artifacts/0b6f3c1e-2a4d-4e8f-9c1b-5d7e3a2f1c00/x', {
    headers: { cookie: `${SESSION_COOKIE_NAME}=wt_token` },
  });
}

function params(filename: string) {
  return { params: Promise.resolve({ releaseId: '0b6f3c1e-2a4d-4e8f-9c1b-5d7e3a2f1c00', filename }) };
}

function upstream(headers: Record<string, string>) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Uint8Array([60, 115, 118, 103]), { status: 200, headers })));
}

describe('GET /api/portal-artifacts — stored XSS hardening', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ['image/svg+xml', 'evil.svg'],
    ['text/html; charset=utf-8', 'evil.html'],
    ['application/xhtml+xml', 'evil.xhtml'],
    ['application/pdf', 'doc.pdf'],
  ])('forces attachment for %s even when Weave-API (or an old one) says inline or nothing', async (type, filename) => {
    for (const disposition of [undefined, `inline; filename="${filename}"`]) {
      upstream({ 'content-type': type, ...(disposition ? { 'content-disposition': disposition } : {}) });

      const res = await GET(artifactRequest(), params(filename));

      expect(res.status).toBe(200);
      expect(res.headers.get('content-disposition')).toMatch(/^attachment; filename="/);
      expect(res.headers.get('content-security-policy')).toBe("default-src 'none'; sandbox");
      expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    }
  });

  it.each(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])('keeps %s inline so chat images still render', async type => {
    upstream({ 'content-type': type, 'content-disposition': 'inline; filename="diagram"' });

    const res = await GET(artifactRequest(), params('diagram'));

    expect(res.headers.get('content-disposition')).toBe('inline; filename="diagram"');
    expect(res.headers.get('content-security-policy')).toBe("default-src 'none'; sandbox");
  });

  it('builds an inline disposition for an allowlisted image when Weave-API sent none', async () => {
    upstream({ 'content-type': 'image/png' });
    const res = await GET(artifactRequest(), params('diagram.png'));
    expect(res.headers.get('content-disposition')).toBe(`inline; filename="diagram.png"; filename*=UTF-8''diagram.png`);
  });

  it("keeps Weave-API's attachment even for an allowlisted image type", async () => {
    upstream({ 'content-type': 'image/png', 'content-disposition': 'attachment; filename="a.png"' });
    const res = await GET(artifactRequest(), params('a.png'));
    expect(res.headers.get('content-disposition')).toBe('attachment; filename="a.png"');
  });

  it('encodes a non-ASCII or quote-carrying filename safely', async () => {
    upstream({ 'content-type': 'text/html' });
    const res = await GET(artifactRequest(), params('ü"x\'.html'));
    expect(res.headers.get('content-disposition')).toBe(`attachment; filename="__x'.html"; filename*=UTF-8''%C3%BC%22x%27.html`);
  });

  it("keeps Weave-API's 'private, no-cache' so a revoked grant takes effect immediately", async () => {
    upstream({ 'content-type': 'image/png', 'cache-control': 'private, no-cache' });
    const res = await GET(artifactRequest(), params('diagram.png'));
    expect(res.headers.get('cache-control')).toBe('private, no-cache');
  });
});
