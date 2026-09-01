import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetch } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { ChatRequestBody } from '@/types/weave-api';

export const dynamic = 'force-dynamic';

/**
 * POST /api/chat/stream — proxies Weave-API's POST /v1/chat/stream.
 *
 * This is the one Route Handler that does NOT decode the upstream body at
 * all: everything up to and including the `Authorization` header is this
 * server's own business (see weave-api-server.ts's module docstring), but
 * from the moment Weave-API's response headers come back, the SSE BYTES
 * themselves are relayed to the browser completely untouched — this
 * route never parses `data:` lines or JSON-decodes an event. That parsing
 * happens once, client-side, in src/lib/sse.ts. Piping the raw stream
 * through here keeps this proxy simple and avoids ever re-serializing
 * (and possibly subtly reshaping) an event Weave-Runtime already produced
 * correctly.
 *
 * Two distinct outcomes, matching Weave-API's own chat_stream() contract
 * (see that route's docstring in Weave-API backend/app/api/chat.py):
 *
 * 1. Weave-API's OWN preparation fails before it ever commits to
 *    `text/event-stream` (missing/expired token, unknown/mismatched
 *    conversation_id, Weave-Runtime unreachable, ...) — a normal HTTP
 *    status with a JSON body. This route mirrors that exactly: a JSON
 *    error response, no stream at all, so the client can tell "the turn
 *    never started" apart from "it started and then failed" without
 *    needing to inspect a partial SSE body.
 * 2. Weave-API answers `200 text/event-stream` — from here on this route
 *    is a transparent pipe (see above); a later in-band `error` event or
 *    a silently dropped connection are BOTH still `200` as far as this
 *    route and the browser's `fetch()` are concerned, exactly as
 *    documented upstream. The client-side stream consumer
 *    (run-chat-stream.ts) is what tells those apart.
 */
export async function POST(request: NextRequest): Promise<Response> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  let body: ChatRequestBody;
  try {
    body = (await request.json()) as ChatRequestBody;
  } catch {
    return NextResponse.json(errorForHttpStatus(422, null), { status: 400 });
  }

  if (!body || typeof body.bot_id !== 'string' || typeof body.message !== 'string' || !body.message.trim()) {
    return NextResponse.json(errorForHttpStatus(422, null), { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await weaveApiFetch('/v1/chat/stream', session.credential, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    if (cause instanceof GatewayError) {
      return NextResponse.json(errorForNetworkFailure(cause.detail), { status: 502 });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }

  if (!upstream.ok) {
    // Prepared-turn failure (case 1 above) — read the small JSON error
    // body and pass its meaning through as our own JSON response, never
    // as a stream.
    let detail: string | null = null;
    try {
      const parsed: unknown = await upstream.json();
      if (parsed && typeof parsed === 'object' && typeof (parsed as { detail?: unknown }).detail === 'string') {
        detail = (parsed as { detail: string }).detail;
      }
    } catch {
      // Non-JSON body — no detail to carry forward.
    }
    const status = upstream.status === 401 ? 401 : upstream.status < 500 ? upstream.status : 502;
    return NextResponse.json(errorForHttpStatus(upstream.status, detail, 'chat'), { status });
  }

  // Case 2: relay the stream byte-for-byte. Only a small, explicit allow-
  // list of headers is copied — never the full upstream header set — so
  // nothing Weave-API might ever add gets forwarded by accident.
  const headers = new Headers({
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  const conversationId = upstream.headers.get('x-conversation-id');
  if (conversationId) {
    headers.set('X-Conversation-Id', conversationId);
  }

  return new Response(upstream.body, { status: 200, headers });
}
