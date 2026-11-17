import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { weaveApiFetch } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import { rejectCrossSiteRequest } from '@/lib/request-guard';
import type { FeedbackRating, FeedbackRequestBody } from '@/types/weave-api';

/**
 * PUT/DELETE /api/messages/{id}/feedback — proxies Weave-API's answer
 * feedback (PUT/DELETE /v1/messages/{message_id}/feedback,
 * backend/app/api/feedback.py there): rate one assistant answer
 * (`helpful` | `wrong` | `outdated`, optional comment) or take the rating
 * back. Ownership is enforced entirely upstream — someone else's message,
 * a user message and an unknown id are all the same 404 there; this proxy
 * adds no authorization of its own, only the CSRF guard every
 * state-changing route has (lib/request-guard.ts) and a shape check that
 * spares Weave-API an obviously malformed call.
 */

const RATINGS: ReadonlySet<FeedbackRating> = new Set(['helpful', 'wrong', 'outdated']);
/** Weave-API's FEEDBACK_COMMENT_MAX_LENGTH (backend/app/schemas/feedback.py). */
const FEEDBACK_COMMENT_MAX_LENGTH = 2000;
// `messages.id` is a positive integer; anything else can never match.
const MESSAGE_ID_RE = /^[1-9]\d{0,9}$/;

function notFound(): NextResponse {
  return NextResponse.json(errorForHttpStatus(404, null), { status: 404 });
}

function upstreamError(status: number, detail: string | null): NextResponse {
  const mapped = status >= 500 ? 502 : status;
  return NextResponse.json(errorForHttpStatus(mapped, detail), { status: mapped });
}

function parseBody(payload: unknown): FeedbackRequestBody | null {
  if (!payload || typeof payload !== 'object') return null;
  const { rating, comment } = payload as { rating?: unknown; comment?: unknown };
  if (typeof rating !== 'string' || !RATINGS.has(rating as FeedbackRating)) return null;
  if (comment !== undefined && comment !== null && typeof comment !== 'string') return null;
  if (typeof comment === 'string' && comment.trim().length > FEEDBACK_COMMENT_MAX_LENGTH) return null;
  return { rating: rating as FeedbackRating, comment: typeof comment === 'string' ? comment : null };
}

export async function PUT(request: NextRequest, { params }: { params: Promise<{ id: string }> }): Promise<NextResponse> {
  const forbidden = rejectCrossSiteRequest(request, { json: true });
  if (forbidden) return forbidden;
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;
  const { id } = await params;
  if (!MESSAGE_ID_RE.test(id)) return notFound();

  const body = parseBody(await request.json().catch(() => null));
  if (!body) return NextResponse.json(errorForHttpStatus(422, null), { status: 422 });

  try {
    const response = await weaveApiFetch(`/v1/messages/${id}/feedback`, session.credential, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => null);
    if (response.ok) return NextResponse.json(payload, { status: 200 });
    return upstreamError(response.status, typeof payload?.detail === 'string' ? payload.detail : null);
  } catch (cause) {
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}

export async function DELETE(request: NextRequest, { params }: { params: Promise<{ id: string }> }): Promise<NextResponse> {
  const forbidden = rejectCrossSiteRequest(request);
  if (forbidden) return forbidden;
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;
  const { id } = await params;
  if (!MESSAGE_ID_RE.test(id)) return notFound();

  try {
    const response = await weaveApiFetch(`/v1/messages/${id}/feedback`, session.credential, { method: 'DELETE' });
    if (response.status === 204) return new NextResponse(null, { status: 204 });
    return upstreamError(response.status, null);
  } catch (cause) {
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
