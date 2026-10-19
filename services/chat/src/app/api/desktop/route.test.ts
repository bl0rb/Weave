import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET, buildDesktopSetupUrl } from '@/app/api/desktop/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

function desktopRequest({ withCookie = true }: { withCookie?: boolean } = {}): NextRequest {
  const headers: Record<string, string> = {};
  if (withCookie) headers.cookie = `${SESSION_COOKIE_NAME}=wt_token`;
  return new NextRequest('http://localhost/api/desktop', { headers });
}

describe('GET /api/desktop', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('answers 401 without a session', async () => {
    expect((await GET(desktopRequest({ withCookie: false }))).status).toBe(401);
  });

  it('builds the weave:// link from the browser-reachable Weave-API address', async () => {
    vi.stubEnv('WEAVE_API_PUBLIC_BASE_URL', 'https://weave.example.com/api/');
    const body = await (await GET(desktopRequest())).json();
    expect(body.server_url).toBe('https://weave.example.com/api');
    expect(body.setup_url).toBe('weave://setup?server=https%3A%2F%2Fweave.example.com%2Fapi');
    expect(body.download_url).toContain('github.com/bl0rb/Weave/releases');
  });

  it('never carries anything but the server address', () => {
    expect(buildDesktopSetupUrl('http://localhost:8004')).toBe('weave://setup?server=http%3A%2F%2Flocalhost%3A8004');
  });
});
