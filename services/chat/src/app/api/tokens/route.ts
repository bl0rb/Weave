import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { weaveApiFetch } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';

/**
 * GET/POST /api/tokens — proxies Weave-API's self-service personal API
 * tokens (GET/POST /v1/me/tokens). Weave-API only lets a signed-in
 * session manage tokens, never a bearer token; the upstream 403 for a
 * token login is passed through so the dialog can explain it.
 */
async function forward(request: NextRequest, init?: RequestInit): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;
  try {
    const response = await weaveApiFetch('/v1/me/tokens', session.credential, init);
    const body = await response.json().catch(() => null);
    if (response.ok) return NextResponse.json(body, { status: response.status });
    const status = response.status >= 500 ? 502 : response.status;
    return NextResponse.json(errorForHttpStatus(status, typeof body?.detail === 'string' ? body.detail : null), { status });
  } catch (cause) {
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  return forward(request);
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const payload = await request.json().catch(() => ({}));
  return forward(request, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
}
