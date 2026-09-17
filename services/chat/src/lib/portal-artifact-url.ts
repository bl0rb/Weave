/**
 * Rewrites a released document's absolute image URL (Weave-Ingest's
 * `{public_api_url}/api/v1/portal/releases/{releaseId}/artifacts/{filename}`,
 * as it reaches this UI verbatim in a `Source.images` entry or inline in a
 * bot's answer markdown) into this app's own same-origin proxy route
 * (`/api/portal-artifacts/{releaseId}/{filename}`, see that route's own
 * docstring) — the browser never talks to Weave-Ingest directly.
 *
 * Any URL that doesn't match this exact shape is left untouched: this is a
 * best-effort rewrite for the one known image-URL shape, not a general
 * allow-list — an unrelated http(s) image already renders fine on its own.
 */
const RELEASE_ARTIFACT_PATH_RE = /\/api\/v1\/portal\/releases\/([^/?#]+)\/artifacts\/([^/?#]+)/;

export function toProxiedImageUrl(url: string): string {
  const match = RELEASE_ARTIFACT_PATH_RE.exec(url);
  if (!match) return url;
  const [, releaseId, filename] = match;
  return `/api/portal-artifacts/${releaseId}/${filename}`;
}
