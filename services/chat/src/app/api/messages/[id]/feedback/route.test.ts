import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { DELETE, PUT } from '@/app/api/messages/[id]/feedback/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const TOKEN = 'wt_super_secret_personal_token';
type FetchMock = ReturnType<typeof vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>>;

function feedbackRequest(method: 'PUT' | 'DELETE', body?: unknown, { withCookie = true } = {}): NextRequest {
  const headers: Record<string, string> = {};
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=${TOKEN}`;
  if (method === 'PUT') headers['content-type'] = 'application/json';
  return new NextRequest('http://localhost/api/messages/42/feedback', {
    method,
    headers,
    body: method === 'PUT' ? JSON.stringify(body) : undefined,
  });
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

function stubFetch(response: () => Response): FetchMock {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => response());
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('PUT /api/messages/{id}/feedback', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('answers 401 without calling Weave-API when the session cookie is missing', async () => {
    const fetchMock = stubFetch(() => json({}));
    const res = await PUT(feedbackRequest('PUT', { rating: 'helpful' }, { withCookie: false }), params('42'));
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('forwards the rating and comment to Weave-API with the session credential and relays the stored state', async () => {
    const stored = { message_id: 42, rating: 'wrong', comment: 'Frist ist 14 Tage', updated_at: '2026-11-17T10:00:00Z' };
    const fetchMock = stubFetch(() => json(stored));

    const res = await PUT(feedbackRequest('PUT', { rating: 'wrong', comment: 'Frist ist 14 Tage', user_id: 99 }), params('42'));

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(stored);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://localhost:8004/v1/messages/42/feedback');
    expect(init?.method).toBe('PUT');
    expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    // Only the two documented fields reach Weave-API — nothing else the
    // browser sent is passed through.
    expect(JSON.parse(init?.body as string)).toEqual({ rating: 'wrong', comment: 'Frist ist 14 Tage' });
  });

  it('sends comment: null when none was given', async () => {
    const fetchMock = stubFetch(() => json({ message_id: 42, rating: 'helpful', comment: null, updated_at: 'x' }));
    await PUT(feedbackRequest('PUT', { rating: 'helpful' }), params('42'));
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({ rating: 'helpful', comment: null });
  });

  it.each([
    [{ rating: 'great' }],
    [{}],
    [{ rating: 'wrong', comment: 42 }],
    [{ rating: 'wrong', comment: 'x'.repeat(2001) }],
    [null],
  ])('rejects the malformed body %j with 422 without calling Weave-API', async (body) => {
    const fetchMock = stubFetch(() => json({}));
    const res = await PUT(feedbackRequest('PUT', body), params('42'));
    expect(res.status).toBe(422);
    expect((await res.json()).kind).toBe('validation_error');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('accepts a comment of exactly 2000 characters', async () => {
    const fetchMock = stubFetch(() => json({ message_id: 42, rating: 'outdated', comment: 'x', updated_at: 'x' }));
    const res = await PUT(feedbackRequest('PUT', { rating: 'outdated', comment: 'x'.repeat(2000) }), params('42'));
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['abc', '0', '-1', '1.5', '..%2Fconversations', '12345678901'])(
    'answers 404 for the non-message id %s without calling Weave-API (no path tricks upstream)',
    async (id) => {
      const fetchMock = stubFetch(() => json({}));
      const res = await PUT(feedbackRequest('PUT', { rating: 'helpful' }), params(id));
      expect(res.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("passes Weave-API's 404 (someone else's or a user message) through as 404", async () => {
    stubFetch(() => json({ detail: 'Message not found' }, 404));
    const res = await PUT(feedbackRequest('PUT', { rating: 'helpful' }), params('42'));
    expect(res.status).toBe(404);
    expect((await res.json()).kind).toBe('rejected');
  });

  it('maps an expired credential to 401 auth_expired and an upstream 5xx to 502', async () => {
    stubFetch(() => json({ detail: 'Not authenticated' }, 401));
    const unauthorized = await PUT(feedbackRequest('PUT', { rating: 'helpful' }), params('42'));
    expect(unauthorized.status).toBe(401);
    expect((await unauthorized.json()).kind).toBe('auth_expired');

    stubFetch(() => json({ detail: 'boom' }, 500));
    const failed = await PUT(feedbackRequest('PUT', { rating: 'helpful' }), params('42'));
    expect(failed.status).toBe(502);
    expect((await failed.json()).kind).toBe('gateway_unreachable');
  });

  it('maps a transport failure to 502 gateway_unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));
    const res = await PUT(feedbackRequest('PUT', { rating: 'helpful' }), params('42'));
    expect(res.status).toBe(502);
    expect((await res.json()).kind).toBe('gateway_unreachable');
  });
});

describe('DELETE /api/messages/{id}/feedback', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('answers 401 without calling Weave-API when the session cookie is missing', async () => {
    const fetchMock = stubFetch(() => new Response(null, { status: 204 }));
    const res = await DELETE(feedbackRequest('DELETE', undefined, { withCookie: false }), params('42'));
    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('withdraws the rating upstream and answers 204', async () => {
    const fetchMock = stubFetch(() => new Response(null, { status: 204 }));
    const res = await DELETE(feedbackRequest('DELETE'), params('42'));
    expect(res.status).toBe(204);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('http://localhost:8004/v1/messages/42/feedback');
    expect(init?.method).toBe('DELETE');
  });

  it('answers 404 for a malformed id without calling Weave-API, and passes an upstream 404 through', async () => {
    const fetchMock = stubFetch(() => json({ detail: 'Message not found' }, 404));
    expect((await DELETE(feedbackRequest('DELETE'), params('x'))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await DELETE(feedbackRequest('DELETE'), params('7'))).status).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('maps an upstream 5xx to 502', async () => {
    stubFetch(() => json({ detail: 'boom' }, 503));
    expect((await DELETE(feedbackRequest('DELETE'), params('42'))).status).toBe(502);
  });
});
