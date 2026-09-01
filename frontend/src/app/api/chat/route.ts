import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { ChatRequestBody, ChatResponseBody } from '@/types/weave-api';

/**
 * POST /api/chat — proxies Weave-API's non-streaming POST /v1/chat. The
 * UI's fallback path: used when the streaming turn (`/api/chat/stream`)
 * could not even be started (Weave-API/Weave-Runtime unreachable), never
 * as the default — see the chat client's own retry logic.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
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

  try {
    const result = await weaveApiFetchJson<ChatResponseBody>('/v1/chat', session.credential, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return NextResponse.json(result);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.kind === 'unauthorized' ? 401 : cause.status && cause.status < 500 ? cause.status : 502;
      return NextResponse.json(errorForHttpStatus(cause.status ?? status, cause.detail, 'chat'), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
