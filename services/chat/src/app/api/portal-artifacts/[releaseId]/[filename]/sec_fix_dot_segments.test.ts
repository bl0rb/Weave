import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const REL = '0b6f3c1e-2a4d-4e8f-9c1b-5d7e3a2f1c00';

function artifactRequest(): NextRequest {
  return new NextRequest('http://localhost/api/portal-artifacts/x/y', {
    headers: { cookie: `${SESSION_COOKIE_NAME}=wt_token` },
  });
}

describe('GET /api/portal-artifacts — dot-segment route params', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ['..', '..'],
    [REL, '..'],
    ['..', 'a.png'],
    ['.', 'a.png'],
    [REL, '.'],
    [REL, ''],
    // Separators/query/control characters: a decoding proxy between here
    // and Weave-API would otherwise turn them back into path structure.
    [REL, 'a/../../me'],
    [REL, '..\\me'],
    [REL, 'a.png?x=1'],
    [REL, 'a.png#x'],
    [REL, 'a\u0000.png'],
    [REL, 'a\n.png'],
    [REL, 'a\u007f.png'],
    // releaseId must be a UUID.
    ['a/../../../me', 'a.png'],
    ['rel-1', 'a.png'],
    [`${REL}/..`, 'a.png'],
    ['', 'a.png'],
  ])('answers 404 for releaseId=%j filename=%j without calling Weave-API', async (releaseId, filename) => {
    const fetchMock = vi.fn(async () => new Response(new Uint8Array([1]), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(artifactRequest(), { params: Promise.resolve({ releaseId, filename }) });

    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('still proxies an ordinary filename that merely contains dots', async () => {
    const fetchMock = vi.fn(async () => new Response(new Uint8Array([1]), { status: 200, headers: { 'content-type': 'image/png' } }));
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(artifactRequest(), { params: Promise.resolve({ releaseId: REL, filename: '..a.png' }) });

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
