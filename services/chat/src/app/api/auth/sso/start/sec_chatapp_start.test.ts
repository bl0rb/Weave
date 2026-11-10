import { createHash } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '@/app/api/auth/sso/start/route';
import { SSO_CALLBACK_PATH, buildSsoStartPath } from '@/lib/sso';
import { SSO_PKCE_COOKIE_NAME } from '@/lib/sso-pkce';

function startRequest(provider: string, headers: Record<string, string> = {}): NextRequest {
  return new NextRequest(`http://localhost/api/auth/sso/start?provider=${provider}`, { headers });
}

function pkceCookie(res: Response): string {
  const raw = res.headers.get('set-cookie') ?? '';
  const match = raw.match(new RegExp(`${SSO_PKCE_COOKIE_NAME}=([^;]*)`));
  expect(match).not.toBeNull();
  return match![1];
}

describe('GET /api/auth/sso/start', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ['ingest', 'WEAVE_API_INGEST_LOGIN_ENABLED', '/v1/auth/ingest/login'],
    ['oidc', 'WEAVE_API_OIDC_ENABLED', '/v1/auth/oidc/login'],
  ])('provider=%s: sets the verifier cookie and redirects with code_challenge = S256(verifier)', async (provider, flag, path) => {
    vi.stubEnv(flag, 'true');
    vi.stubEnv('WEAVE_API_PUBLIC_BASE_URL', 'https://api.example.com');
    vi.stubEnv('APP_BASE_URL', 'https://chat.example.com');

    const res = await GET(startRequest(provider));

    expect(res.status).toBe(307);
    const target = new URL(res.headers.get('location')!);
    expect(`${target.origin}${target.pathname}`).toBe(`https://api.example.com${path}`);
    expect(target.searchParams.get('return_to')).toBe(`https://chat.example.com${SSO_CALLBACK_PATH}`);

    const verifier = pkceCookie(res);
    expect(verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const challenge = createHash('sha256').update(verifier).digest('base64url');
    expect(target.searchParams.get('code_challenge')).toBe(challenge);
    // The verifier itself never leaves this app's cookie.
    expect(res.headers.get('location')).not.toContain(verifier);

    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toMatch(/HttpOnly/i);
    expect(setCookie).toMatch(/SameSite=Lax/i);
    expect(setCookie).toMatch(/Path=\/api\/auth\/sso/);
    expect(setCookie).toMatch(/Max-Age=600/);
    expect(setCookie).not.toMatch(/Secure/i);
  });

  it('mints a fresh verifier per login', async () => {
    vi.stubEnv('WEAVE_API_INGEST_LOGIN_ENABLED', 'true');
    const first = pkceCookie(await GET(startRequest('ingest')));
    const second = pkceCookie(await GET(startRequest('ingest')));
    expect(first).not.toBe(second);
  });

  it('marks the cookie Secure behind an https proxy', async () => {
    vi.stubEnv('WEAVE_API_INGEST_LOGIN_ENABLED', 'true');
    const res = await GET(startRequest('ingest', { 'x-forwarded-proto': 'https' }));
    expect(res.headers.get('set-cookie')).toMatch(/Secure/i);
  });

  it.each([
    ['oidc', ''],
    ['ingest', ''],
    ['something-else', 'true'],
  ])('answers 404 without a cookie for provider=%s when that login is not enabled', async (provider, enabled) => {
    vi.stubEnv('WEAVE_API_OIDC_ENABLED', enabled);
    vi.stubEnv('WEAVE_API_INGEST_LOGIN_ENABLED', enabled);

    const res = await GET(startRequest(provider));

    expect(res.status).toBe(404);
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it('is what the /login buttons link to', () => {
    expect(buildSsoStartPath('oidc')).toBe('/api/auth/sso/start?provider=oidc');
    expect(buildSsoStartPath('ingest')).toBe('/api/auth/sso/start?provider=ingest');
  });
});
