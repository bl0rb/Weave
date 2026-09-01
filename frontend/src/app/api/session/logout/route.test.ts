import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { POST } from '@/app/api/session/logout/route';
import { SESSION_COOKIE_NAME, SESSION_KIND_COOKIE_NAME } from '@/lib/session';

function logoutRequest(cookie?: string): NextRequest {
  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = cookie;
  return new NextRequest('http://localhost/api/session/logout', { method: 'POST', headers });
}

describe('POST /api/session/logout', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('for a Personal-API-Token (bearer) session: clears this app\'s own cookie and never calls Weave-API at all', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(logoutRequest(`${SESSION_COOKIE_NAME}=wt_some_personal_token`));

    expect(res.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toContain(`${SESSION_COOKIE_NAME}=;`);
    expect(setCookie).toContain(`${SESSION_KIND_COOKIE_NAME}=;`);
  });

  it('for an SSO-derived (session) credential: calls Weave-API\'s own POST /v1/auth/logout with the token as a Cookie header, then clears this app\'s own cookie', async () => {
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      expect(String(url)).toBe('http://localhost:8004/v1/auth/logout');
      expect(init?.method).toBe('POST');
      expect((init?.headers as Record<string, string>).Cookie).toBe('weave_api_session=sess-token-abc');
      return new Response(JSON.stringify({ status: 'ok' }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(
      logoutRequest(`${SESSION_COOKIE_NAME}=sess-token-abc; ${SESSION_KIND_COOKIE_NAME}=session`)
    );

    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toContain(`${SESSION_COOKIE_NAME}=;`);
    expect(setCookie).toContain(`${SESSION_KIND_COOKIE_NAME}=;`);
  });

  it('still clears this app\'s own cookie even when the gateway logout call fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed: ECONNREFUSED');
      })
    );

    const res = await POST(
      logoutRequest(`${SESSION_COOKIE_NAME}=sess-token-abc; ${SESSION_KIND_COOKIE_NAME}=session`)
    );

    expect(res.status).toBe(200);
    const setCookie = res.headers.get('set-cookie') ?? '';
    expect(setCookie).toContain(`${SESSION_COOKIE_NAME}=;`);
  });

  it('with no session cookie at all: still answers ok and never calls Weave-API', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await POST(logoutRequest());

    expect(res.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
