import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { BRANDING_EXTENSIONS, isBrandingAssetKind, isBrandingContentType, type BrandingAssetKind, type BrandingContentType } from '@/lib/branding';
import { errorForHttpStatus, errorForNetworkFailure } from '@/lib/errors';
import { GatewayError, fetchBrandingAsset } from '@/lib/weave-api-server';

/**
 * GET /api/branding/assets/{kind} -- proxies Weave-API's public
 * GET /v1/branding/assets/{kind} (the admin's logo or busy animation, kind
 * logo|busy). Public on purpose: the login page shows the logo too, so no
 * session and no credential (the asset is the same for everyone); GET only,
 * so no CSRF guard. Only this one Weave-API path is ever fetched.
 *
 * The bytes answer on the chat's own origin, next to its session cookie,
 * and an SVG can carry script. The chat only ever shows them via `<img>`
 * (no script runs there), and this route re-checks the type itself instead
 * of trusting upstream: SVG/GIF/PNG only, `nosniff`, and a CSP that
 * sandboxes the document should anyone open the URL directly. Response
 * headers are built here from scratch, so nothing else from upstream
 * (Set-Cookie above all) is ever forwarded.
 */
export async function GET(request: NextRequest, context: { params: Promise<{ kind: string }> }): Promise<NextResponse> {
  const { kind } = await context.params;
  if (!isBrandingAssetKind(kind)) {
    return NextResponse.json(errorForHttpStatus(404, null), { status: 404 });
  }

  try {
    const response = await fetchBrandingAsset(kind, request.headers.get('if-none-match'));
    const etag = response.headers.get('etag');
    if (response.status === 304) {
      return new NextResponse(null, { status: 304, headers: cacheHeaders(etag) });
    }
    if (response.status === 404) {
      return NextResponse.json(errorForHttpStatus(404, null), { status: 404 });
    }
    if (response.status !== 200) {
      return NextResponse.json(errorForHttpStatus(502, null), { status: 502 });
    }
    const contentType = (response.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase();
    const declaredLength = Number(response.headers.get('content-length') ?? '0');
    if (!isBrandingContentType(contentType) || declaredLength > MAX_ASSET_BYTES) {
      return NextResponse.json(errorForHttpStatus(502, null), { status: 502 });
    }
    const body = await response.arrayBuffer();
    if (body.byteLength > MAX_ASSET_BYTES) {
      return NextResponse.json(errorForHttpStatus(502, null), { status: 502 });
    }
    return new NextResponse(body, { status: 200, headers: assetHeaders(kind, contentType, etag) });
  } catch (cause) {
    if (cause instanceof GatewayError) {
      return NextResponse.json(errorForHttpStatus(502, cause.detail), { status: 502 });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}

/** Same limit as the upload in Ingest and the proxy in Weave-API. */
const MAX_ASSET_BYTES = 1024 * 1024;

// Weave-API's own value: no subresources except inline styles and data:
// images an SVG may embed, no script, an opaque origin.
const ASSET_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox";

function cacheHeaders(etag: string | null): Record<string, string> {
  return {
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': ASSET_CSP,
    'Cache-Control': 'public, max-age=300',
    ...(etag ? { ETag: etag } : {}),
  };
}

function assetHeaders(kind: BrandingAssetKind, contentType: BrandingContentType, etag: string | null): Record<string, string> {
  return {
    'Content-Type': contentType,
    'Content-Disposition': `inline; filename="branding-${kind}.${BRANDING_EXTENSIONS[contentType]}"`,
    ...cacheHeaders(etag),
  };
}
