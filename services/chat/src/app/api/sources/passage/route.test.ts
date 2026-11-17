import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '@/app/api/sources/passage/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const TOKEN = 'wt_super_secret_personal_token';
const DOC = '1b4e28ba-2fa1-11d2-883f-0016d3cca427';

function passageRequest(query: string, { withCookie = true } = {}): NextRequest {
  const headers: Record<string, string> = {};
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=${TOKEN}`;
  return new NextRequest(`http://localhost/api/sources/passage?${query}`, { headers });
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const PASSAGE = {
  document_id: DOC, source_job_id: 'job-1', title: 'Reiserichtlinie', original_filename: 'reise.pdf', document_version: 3,
  source_kind: 'upload', source_url: null, collection: 'hr', target_chunk_id: 7,
  chunks: [{ chunk_id: 7, chunk_index: 2, heading_path: ['Reisekosten'], page_start: 4, page_end: 4, text: 'Tagegeld …', is_target: true }],
};

describe('GET /api/sources/passage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('answers 401 without calling Weave-API when the session cookie is missing', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const res = await GET(passageRequest(`document_id=${DOC}&chunk_id=7`, { withCookie: false }));
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('proxies to Weave-API with the session credential, defaulting context to 1, and relays the passage', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => json(PASSAGE));
    vi.stubGlobal('fetch', fetchMock);

    const res = await GET(passageRequest(`document_id=${DOC}&chunk_id=7`));

    expect(res.status).toBe(200);
    expect(await res.clone().text()).not.toContain(TOKEN);
    expect(await res.json()).toEqual(PASSAGE);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`http://localhost:8004/v1/sources/passage?document_id=${DOC}&chunk_id=7&context=1`);
    expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it('passes an explicit context through', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => json(PASSAGE));
    vi.stubGlobal('fetch', fetchMock);
    await GET(passageRequest(`document_id=${DOC}&chunk_id=7&context=3`));
    expect(String(fetchMock.mock.calls[0][0])).toContain('context=3');
  });

  it.each([
    'chunk_id=7',
    `document_id=${DOC}`,
    'document_id=not-a-uuid&chunk_id=7',
    `document_id=${DOC}&chunk_id=-1`,
    `document_id=${DOC}&chunk_id=7%26context%3D3`,
    `document_id=${DOC}%2F..%2Fx&chunk_id=7`,
  ])('answers 404 for malformed ids (%s) without calling Weave-API', async (query) => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const res = await GET(passageRequest(query));
    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['4', '-1', '1.5', 'x'])('answers 422 for an out-of-range context %s without calling Weave-API', async (context) => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const res = await GET(passageRequest(`document_id=${DOC}&chunk_id=7&context=${context}`));
    expect(res.status).toBe(422);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes Weave-API's 404 (not visible / gone / superseded) through as 404", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'Passage not found' }, 404)));
    const res = await GET(passageRequest(`document_id=${DOC}&chunk_id=7`));
    expect(res.status).toBe(404);
  });

  it('maps 401 to auth_expired, an upstream 502 and a transport failure to 502', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'Not authenticated' }, 401)));
    const unauthorized = await GET(passageRequest(`document_id=${DOC}&chunk_id=7`));
    expect(unauthorized.status).toBe(401);
    expect((await unauthorized.json()).kind).toBe('auth_expired');

    vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'Weave-Retrieval unreachable' }, 502)));
    expect((await GET(passageRequest(`document_id=${DOC}&chunk_id=7`))).status).toBe(502);

    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));
    const unreachable = await GET(passageRequest(`document_id=${DOC}&chunk_id=7`));
    expect(unreachable.status).toBe(502);
    expect((await unreachable.json()).kind).toBe('gateway_unreachable');
  });
});
