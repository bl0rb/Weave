import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetch, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { ConversationSummary } from '@/types/weave-api';

/**
 * GET /api/conversations — proxies Weave-API's GET /v1/conversations: the
 * history sidebar's list of this user's own past conversations, most
 * recently active first. Unwraps Weave-API's `{ items: [...] }` envelope
 * (`ConversationListResponse`, backend/app/schemas/conversations.py) into
 * a plain array — the shape every other list proxy in this app (bots,
 * collections) already returns.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  try {
    const { items } = await weaveApiFetchJson<{ items: ConversationSummary[] }>('/v1/conversations', session.credential);
    return NextResponse.json(items);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.kind === 'unauthorized' ? 401 : 502;
      return NextResponse.json(errorForHttpStatus(cause.status ?? status, cause.detail), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}

/** DELETE /api/conversations — permanently removes the caller's complete
 * history. The upstream route is ownership-scoped and returns 204. */
export async function DELETE(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  try {
    const response = await weaveApiFetch('/v1/conversations', session.credential, { method: 'DELETE' });
    if (response.status === 204) return new NextResponse(null, { status: 204 });
    if (response.status === 401) return NextResponse.json(errorForHttpStatus(401, null), { status: 401 });
    if (response.status >= 500) return NextResponse.json(errorForHttpStatus(502, null), { status: 502 });
    return NextResponse.json(errorForHttpStatus(response.status, null), { status: response.status });
  } catch (cause) {
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
