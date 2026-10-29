import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { resolveWeaveApiPublicBaseUrl } from '@/lib/sso';

/** The newest desktop release with its installers (bl0rb/WeaveDesktop). */
export const DESKTOP_DOWNLOAD_URL = 'https://github.com/bl0rb/WeaveDesktop/releases/latest';

/**
 * The `weave://setup?server=<url>` link the desktop app registers (see
 * src-tauri/src/deeplink.rs in bl0rb/WeaveDesktop): only the server address
 * travels in it, never a token -- the app asks the user before saving it.
 */
export function buildDesktopSetupUrl(serverUrl: string): string {
  return `weave://setup?${new URLSearchParams({ server: serverUrl }).toString()}`;
}

/**
 * GET /api/desktop — what the "Desktop-App einrichten" dialog needs: the
 * browser-reachable Weave-API address (`WEAVE_API_PUBLIC_BASE_URL`, the
 * same one the SSO link navigates to, which is exactly what the desktop
 * app asks for as its "API-Adresse"), the `weave://` link built from it,
 * and the download page. Server-side because the client bundle never
 * sees that environment variable (see lib/sso.ts).
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;
  const serverUrl = resolveWeaveApiPublicBaseUrl();
  return NextResponse.json({
    server_url: serverUrl,
    setup_url: buildDesktopSetupUrl(serverUrl),
    download_url: DESKTOP_DOWNLOAD_URL,
  });
}
