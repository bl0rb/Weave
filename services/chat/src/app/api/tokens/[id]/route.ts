import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { weaveApiFetch } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';

/** DELETE /api/tokens/{id} — revokes one of the caller's own personal API
 * tokens (Weave-API's DELETE /v1/me/tokens/{id}, ownership-scoped). */
export async function DELETE(request: NextRequest, { params }: { params: Promise<{ id: string }> }): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;
  const { id } = await params;
  try {
    const response = await weaveApiFetch(`/v1/me/tokens/${encodeURIComponent(id)}`, session.credential, { method: 'DELETE' });
    if (response.status === 204) return new NextResponse(null, { status: 204 });
    const status = response.status >= 500 ? 502 : response.status;
    return NextResponse.json(errorForHttpStatus(status, null), { status });
  } catch (cause) {
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
