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
  // encodeURIComponent leaves '.' and '..' as they are, and fetch's URL
  // parsing would then collapse them as dot segments — a GET to some other
  // Weave-API path with the caller's credential, never reaching its own
  // `_valid_filename` check. Same rule as that check, applied here first —
  // separators included, since a proxy in between that decodes %2F would
  // otherwise turn 'a/../..' into dot segments after all. A release id is
  // always a UUID.
  if (!UUID_PATTERN.test(releaseId) || !isValidFilename(filename)) {
    return NextResponse.json(errorForHttpStatus(404, null), { status: 404 });
  }

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
    const contentType = response.headers.get('content-type') ?? 'application/octet-stream';
    return new NextResponse(body, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Content-Disposition': contentDisposition(contentType, response.headers.get('content-disposition'), filename),
        'Content-Security-Policy': ARTIFACT_CSP,
        'X-Content-Type-Options': 'nosniff',
        // Weave-API's own value, kept: every view revalidates, so revoking a
        // grant also stops images the browser already holds. A max-age here
        // would keep serving them from cache for that long.
        'Cache-Control': 'private, no-cache',
      },
    });
  } catch (cause) {
    if (cause instanceof GatewayError) {
      return NextResponse.json(errorForHttpStatus(502, cause.detail), { status: 502 });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}

const UUID_PATTERN = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;

// Weave-API's portal_artifacts.py `_valid_filename`: one plain path segment.
function isValidFilename(value: string): boolean {
  return value !== '' && value !== '.' && value !== '..' && !/[/\\?#\x00-\x1f\x7f]/.test(value);
}

// The same allowlist as Weave-API's portal_artifacts.py: only raster images
// render inline. Everything else — SVG (which carries script) and HTML
// above all — is an attachment, because this route answers on the chat's
// own origin, next to its session cookie: an uploaded document's active
// content rendered here would be stored XSS against every chat user.
const INLINE_CONTENT_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

// Defence in depth for whatever still renders (a navigation the browser
// renders despite 'attachment', a type sniffed despite nosniff): no
// subresources, no script, an opaque origin. `<img>` in the chat ignores an
// image's own CSP, so inline images keep working.
const ARTIFACT_CSP = "default-src 'none'; sandbox";

/** Weave-API's Content-Disposition when it is safe to keep, else this
 * route's own: inline only for an allowlisted type that Weave-API itself
 * offered inline (or without saying), 'attachment' for everything else —
 * never trusting an upstream 'inline' for a type outside the allowlist. */
function contentDisposition(contentType: string, upstream: string | null, filename: string): string {
  const mediaType = contentType.split(';', 1)[0].trim().toLowerCase();
  const upstreamAttachment = (upstream ?? '').trim().toLowerCase().startsWith('attachment');
  const inline = INLINE_CONTENT_TYPES.has(mediaType) && !upstreamAttachment;
  const wanted = inline ? 'inline' : 'attachment';
  if (upstream && upstream.trim().toLowerCase().startsWith(wanted)) return upstream;
  // RFC 6266/5987, same shape as Weave-API's `_content_disposition`.
  const fallback = Array.from(filename, ch => {
    const code = ch.charCodeAt(0);
    return code >= 32 && code < 127 && ch !== '"' && ch !== '\\' ? ch : '_';
  }).join('') || 'download';
  let encoded: string;
  try {
    // encodeURIComponent leaves '()*! unescaped; RFC 5987's attr-char doesn't
    // allow the first four.
    encoded = encodeURIComponent(filename).replace(/['()*]/g, ch => `%${ch.charCodeAt(0).toString(16).toUpperCase()}`);
  } catch {
    // A lone surrogate can't be UTF-8 encoded; the ASCII fallback still names it.
    return `${wanted}; filename="${fallback}"`;
  }
  return `${wanted}; filename="${fallback}"; filename*=UTF-8''${encoded}`;
}
