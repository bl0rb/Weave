import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetch } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';

/**
 * GET /api/portal-artifacts/{releaseId}/{filename} — proxies Weave-API's
 * GET /v1/portal/releases/{releaseId}/artifacts/{filename} (itself a
 * proxy onto Weave-Ingest's release-artifact endpoint): the image bytes a
 * source's markdown links to (types/weave-api.ts's `Source.images`).
 * Requires a valid chat session, exactly like every other proxy route
 * here — no arbitrary URL is ever fetched, only this one Weave-API path
 * built from the two route params. Uses the raw `weaveApiFetch` (not
 * `weaveApiFetchJson`): the body is binary image data, not JSON.
 */
export async function GET(
  request: NextRequest,
  context: { params: Promise<{ releaseId: string; filename: string }> },
): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  const { releaseId, filename } = await context.params;

  try {
    const response = await weaveApiFetch(
      `/v1/portal/releases/${encodeURIComponent(releaseId)}/artifacts/${encodeURIComponent(filename)}`,
      session.credential,
    );
    if (!response.ok) {
      const status = response.status === 401 ? 401 : response.status >= 500 ? 502 : response.status;
      return NextResponse.json(errorForHttpStatus(status, null), { status });
    }
    const body = await response.arrayBuffer();
    return new NextResponse(body, {
      status: 200,
      headers: {
        'Content-Type': response.headers.get('content-type') ?? 'application/octet-stream',
        'Cache-Control': 'private, max-age=3600',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  } catch (cause) {
    if (cause instanceof GatewayError) {
      return NextResponse.json(errorForHttpStatus(502, cause.detail), { status: 502 });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
