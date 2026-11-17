import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import type { PassageResponse } from '@/types/weave-api';

/**
 * GET /api/sources/passage?document_id=<uuid>&chunk_id=<int>&context=<0..3>
 * — proxies Weave-API's GET /v1/sources/passage (backend/app/api/sources.py
 * there): the cited chunk plus its neighbours for the "Textstelle öffnen"
 * dialog (components/chat/passage-dialog.tsx). Read authority is decided
 * upstream from the caller's own identity only; a passage outside it, an
 * unknown document/chunk and a superseded version are all the same 404
 * there. Malformed ids answer that same 404 here without a call.
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CHUNK_ID_RE = /^\d{1,18}$/;

export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  const query = request.nextUrl.searchParams;
  const documentId = query.get('document_id') ?? '';
  const chunkId = query.get('chunk_id') ?? '';
  const contextParam = query.get('context');
  if (!UUID_RE.test(documentId) || !CHUNK_ID_RE.test(chunkId)) {
    return NextResponse.json(errorForHttpStatus(404, null), { status: 404 });
  }
  const context = contextParam === null ? 1 : Number(contextParam);
  if (!Number.isInteger(context) || context < 0 || context > 3) {
    return NextResponse.json(errorForHttpStatus(422, null), { status: 422 });
  }

  const upstream = new URLSearchParams({ document_id: documentId, chunk_id: chunkId, context: String(context) });
  try {
    const passage = await weaveApiFetchJson<PassageResponse>(`/v1/sources/passage?${upstream}`, session.credential);
    return NextResponse.json(passage);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.kind === 'unauthorized' ? 401 : cause.kind === 'rejected' ? (cause.status ?? 404) : 502;
      return NextResponse.json(errorForHttpStatus(status, cause.detail), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
