import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { POST } from '@/app/api/chat/stream/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

const TOKEN = 'wt_super_secret_personal_token';

function streamRequest(body: unknown, { withCookie = true }: { withCookie?: boolean } = {}): NextRequest {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=${TOKEN}`;
  return new NextRequest('http://localhost/api/chat/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
}

function sseUpstream(body: string, extraHeaders: Record<string, string> = {}): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'content-type': 'text/event-stream', ...extraHeaders },
  });
}

describe('POST /api/chat/stream', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('answers 401 without ever calling fetch when there is no session cookie', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(streamRequest({ bot_id: 'legal-support', message: 'hi' }, { withCookie: false }));

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('forwards the cookie token as a Bearer header to Weave-API, relays the SSE body byte-for-byte, and never leaks the token to the browser response', async () => {
    const sseBody =
      'data: {"type":"trace","trace":{"intent":"knowledge","confidence":0.9,"needs_retrieval":true,"needs_tool":false,"retrieval":null,"model":null,"router_mode":"rules","timings_ms":{}}}\n\n' +
      'data: {"type":"delta","text":"Hallo"}\n\n' +
      'data: {"type":"done"}\n\n';

    let capturedAuth: string | null = null;
    let capturedUrl: string | null = null;
    let capturedBody: string | null = null;

    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      capturedUrl = String(input);
      capturedAuth = (init?.headers as Record<string, string>).Authorization ?? null;
      capturedBody = typeof init?.body === 'string' ? init.body : null;
      return sseUpstream(sseBody, { 'x-conversation-id': 'conv-123' });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(streamRequest({ bot_id: 'legal-support', message: 'Welche Kündigungsfrist?' }));

    // --- upstream call was made correctly ---
    expect(capturedUrl).toBe('http://localhost:8004/v1/chat/stream');
    expect(capturedAuth).toBe(`Bearer ${TOKEN}`);
    expect(JSON.parse(capturedBody!)).toEqual({ bot_id: 'legal-support', message: 'Welche Kündigungsfrist?' });

    // --- response to the browser is a transparent, correct relay ---
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/event-stream');
    expect(res.headers.get('x-conversation-id')).toBe('conv-123');

    const relayedText = await res.text();
    expect(relayedText).toBe(sseBody);

    // --- the token must not be observable anywhere in what the browser gets ---
    expect(relayedText).not.toContain(TOKEN);
    for (const [name, value] of res.headers.entries()) {
      expect(name.toLowerCase()).not.toBe('authorization');
      expect(value).not.toContain(TOKEN);
    }
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it('turns a pre-stream rejection (e.g. unknown conversation_id, 404) into a plain JSON error, never a stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'Conversation not found' }), { status: 404 }))
    );

    const res = await POST(streamRequest({ bot_id: 'legal-support', message: 'weiter', conversation_id: 'does-not-exist' }));

    expect(res.status).toBe(404);
    expect(res.headers.get('content-type')).not.toContain('text/event-stream');
    const body = await res.json();
    expect(body.kind).toBe('conversation_not_found');
  });

  it('maps a transport failure reaching Weave-API to 502 gateway_unreachable, not a stream and not a raw error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed: ECONNREFUSED');
      })
    );

    const res = await POST(streamRequest({ bot_id: 'legal-support', message: 'hi' }));

    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.kind).toBe('gateway_unreachable');
    expect(body.message).not.toMatch(/ECONNREFUSED/);
  });

  it('rejects a request with an empty message before ever calling fetch', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(streamRequest({ bot_id: 'legal-support', message: '   ' }));

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
