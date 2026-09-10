import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetch, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { StoredConversation } from '@/types/weave-api';

/**
 * GET /api/conversations/{id} — proxies Weave-API's GET
 * /v1/conversations/{id}: one past conversation's full transcript,
 * fetched only once the history sidebar's user actually opens that entry
 * (see /api/conversations/route.ts for the lightweight list itself).
 * Ownership is enforced entirely upstream (Weave-API scopes the lookup to
 * `Conversation.user_id == caller`, 404 rather than 403 — see that
 * route's own docstring) — this proxy adds no authorization of its own.
 */
export async function GET(request: NextRequest, context: { params: Promise<{ id: string }> }): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  const { id } = await context.params;

  try {
    const conversation = await weaveApiFetchJson<StoredConversation>(`/v1/conversations/${encodeURIComponent(id)}`, session.credential);
    return NextResponse.json(conversation);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.kind === 'unauthorized' ? 401 : cause.kind === 'rejected' ? (cause.status ?? 404) : 502;
      return NextResponse.json(errorForHttpStatus(cause.status ?? status, cause.detail), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}

/**
 * DELETE /api/conversations/{id} — proxies Weave-API's DELETE
 * /v1/conversations/{id} (204 No Content on success). Uses the raw
 * `weaveApiFetch` rather than `weaveApiFetchJson`: a 204 response has no
 * body, and `weaveApiFetchJson`'s unconditional `response.json()` would
 * throw on it.
 */
export async function DELETE(request: NextRequest, context: { params: Promise<{ id: string }> }): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  const { id } = await context.params;

  try {
    const response = await weaveApiFetch(`/v1/conversations/${encodeURIComponent(id)}`, session.credential, { method: 'DELETE' });
    if (response.status === 204) return new NextResponse(null, { status: 204 });
    if (response.status === 401) return NextResponse.json(errorForHttpStatus(401, null), { status: 401 });
    if (response.status >= 500) return NextResponse.json(errorForHttpStatus(502, null), { status: 502 });
    return NextResponse.json(errorForHttpStatus(response.status, null), { status: response.status });
  } catch (cause) {
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
