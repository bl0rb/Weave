import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionToken } from '@/lib/require-session';
import { GatewayError, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { Bot } from '@/types/weave-api';

/** GET /api/bots — proxies Weave-API's GET /v1/bots (the bot roster this
 * user may pick from). Read-only pass-through of the token from the
 * session cookie; see src/lib/weave-api-server.ts for why this proxy hop
 * exists at all. */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionToken(request);
  if ('response' in session) return session.response;

  try {
    const bots = await weaveApiFetchJson<Bot[]>('/v1/bots', session.token);
    return NextResponse.json(bots);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.kind === 'unauthorized' ? 401 : 502;
      return NextResponse.json(errorForHttpStatus(cause.status ?? status, cause.detail), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
