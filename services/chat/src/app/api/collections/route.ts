import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { Collection } from '@/types/weave-api';

/**
 * GET /api/collections — proxies Weave-API's GET /v1/collections: the
 * collections THIS user may read (resolved server-side there from the
 * caller's own team, never a parameter this route could override — see
 * Weave-API backend/app/api/collections.py). Feeds the sidebar's filter:
 * the chosen slugs travel back on the next turn as
 * `ChatRequestBody.collections` (types/weave-api.ts, assembled in
 * lib/chat-types.ts's `buildChatRequestBody`). That filter can only narrow
 * the scope resolved upstream, never widen it.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  try {
    const collections = await weaveApiFetchJson<Collection[]>('/v1/collections', session.credential);
    return NextResponse.json(collections);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.kind === 'unauthorized' ? 401 : 502;
      return NextResponse.json(errorForHttpStatus(cause.status ?? status, cause.detail), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
