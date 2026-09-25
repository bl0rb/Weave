import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { MeResponse } from '@/types/weave-api';

/**
 * GET /api/session/me — proxies Weave-API's GET /v1/me: this user's own
 * account summary (currently just `username` and `locale`). Read by
 * chat-app.tsx once on mount to sync the UI language to whatever the
 * account has saved (see that component's own mount effect) — the same
 * read-only pass-through shape and error mapping as every other proxy in
 * this app (bots.ts, collections).
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  try {
    const me = await weaveApiFetchJson<MeResponse>('/v1/me', session.credential);
    return NextResponse.json(me);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.kind === 'unauthorized' ? 401 : 502;
      return NextResponse.json(errorForHttpStatus(cause.status ?? status, cause.detail), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
