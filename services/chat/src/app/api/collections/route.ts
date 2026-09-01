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
 * Weave-API backend/app/api/collections.py). Purely informational for
 * this UI's sidebar: see the /v1/chat(/stream) request schema
 * (types/weave-api.ts's `ChatRequestBody`) for why there is no collection
 * selection to send back — it has no field for one.
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
