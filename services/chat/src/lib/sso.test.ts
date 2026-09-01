import { afterEach, describe, expect, it, vi } from 'vitest';
import { SSO_CALLBACK_PATH, buildSsoLoginUrl, isSsoLoginEnabled } from '@/lib/sso';

describe('isSsoLoginEnabled — this app\'s OWN setting, since Weave-API exposes no runtime OIDC-config flag', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('is false when WEAVE_API_OIDC_ENABLED is unset (the default: the SSO button stays hidden)', () => {
    vi.stubEnv('WEAVE_API_OIDC_ENABLED', '');
    expect(isSsoLoginEnabled()).toBe(false);
  });

  it('is true for "true"', () => {
    vi.stubEnv('WEAVE_API_OIDC_ENABLED', 'true');
    expect(isSsoLoginEnabled()).toBe(true);
  });

  it('is true for "1"', () => {
    vi.stubEnv('WEAVE_API_OIDC_ENABLED', '1');
    expect(isSsoLoginEnabled()).toBe(true);
  });

  it('is case-insensitive', () => {
    vi.stubEnv('WEAVE_API_OIDC_ENABLED', 'TRUE');
    expect(isSsoLoginEnabled()).toBe(true);
  });

  it('is false for any other value (fails closed, not open)', () => {
    vi.stubEnv('WEAVE_API_OIDC_ENABLED', 'yes');
    expect(isSsoLoginEnabled()).toBe(false);
  });
});

describe('buildSsoLoginUrl', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('points at WEAVE_API_PUBLIC_BASE_URL (the browser-reachable gateway origin), with return_to set to this app\'s own callback route', () => {
    vi.stubEnv('WEAVE_API_PUBLIC_BASE_URL', 'https://api.example.com');
    vi.stubEnv('APP_BASE_URL', 'https://chat.example.com');

    const url = buildSsoLoginUrl();

    const expectedReturnTo = `https://chat.example.com${SSO_CALLBACK_PATH}`;
    const expectedQuery = new URLSearchParams({ return_to: expectedReturnTo }).toString();
    expect(url).toBe(`https://api.example.com/v1/auth/oidc/login?${expectedQuery}`);
  });

  it('falls back to WEAVE_API_BASE_URL (the server-to-server address) when WEAVE_API_PUBLIC_BASE_URL is unset', () => {
    vi.stubEnv('WEAVE_API_BASE_URL', 'https://internal-gateway:8004');
    vi.stubEnv('WEAVE_API_PUBLIC_BASE_URL', '');

    const url = buildSsoLoginUrl();

    expect(url.startsWith('https://internal-gateway:8004/v1/auth/oidc/login?')).toBe(true);
  });

  it('defaults both base URLs to localhost when nothing is configured (local dev)', () => {
    vi.stubEnv('WEAVE_API_BASE_URL', '');
    vi.stubEnv('WEAVE_API_PUBLIC_BASE_URL', '');
    vi.stubEnv('APP_BASE_URL', '');

    const url = buildSsoLoginUrl();

    expect(url.startsWith('http://localhost:8004/v1/auth/oidc/login?')).toBe(true);
    const expectedQuery = new URLSearchParams({ return_to: `http://localhost:3000${SSO_CALLBACK_PATH}` }).toString();
    expect(url).toBe(`http://localhost:8004/v1/auth/oidc/login?${expectedQuery}`);
  });
});
