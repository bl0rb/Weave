import { afterEach, describe, expect, it, vi } from 'vitest';
import { GatewayError, exchangeSessionCode, fetchBranding, fetchBrandingAsset, gatewayLogout, weaveApiFetch } from '@/lib/weave-api-server';

describe('weaveApiFetch — credential-to-header selection (session.ts SessionCredential)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends a `bearer` credential as an Authorization header, never a Cookie', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await weaveApiFetch('/v1/bots', { kind: 'bearer', value: 'personal-token-123' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const headers = init!.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer personal-token-123');
    expect(headers.Cookie).toBeUndefined();
  });

  it('sends a `session` credential as a Cookie header on Weave-API\'s own session cookie name, never Authorization', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await weaveApiFetch('/v1/bots', { kind: 'session', value: 'sso-session-abc' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const headers = init!.headers as Record<string, string>;
    // Weave-API backend/app/core/auth.py's own SESSION_COOKIE_NAME — taken
    // over verbatim, see weave-api-server.ts's GATEWAY_SESSION_COOKIE_NAME.
    expect(headers.Cookie).toBe('weave_api_session=sso-session-abc');
    expect(headers.Authorization).toBeUndefined();
  });
});

describe('exchangeSessionCode', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POSTs the code and PKCE verifier as JSON with no auth header of its own, and returns the session token/expiry', async () => {
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      expect(String(url)).toBe('http://localhost:8004/v1/auth/session/exchange');
      expect(init?.method).toBe('POST');
      expect(JSON.parse(init!.body as string)).toEqual({ code: 'the-one-time-code', code_verifier: 'the-verifier' });
      const headers = (init?.headers ?? {}) as Record<string, string>;
      expect(headers.Authorization).toBeUndefined();
      expect(headers.Cookie).toBeUndefined();
      return new Response(JSON.stringify({ session_token: 'sess-abc', expires_at: '2026-01-01T00:00:00Z' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await exchangeSessionCode('the-one-time-code', 'the-verifier');

    expect(result).toEqual({ sessionToken: 'sess-abc', expiresAt: '2026-01-01T00:00:00Z' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('throws a `rejected` GatewayError on a non-2xx response (unknown/expired/already-used code, indistinguishably)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'invalid or expired code' }), { status: 400 }))
    );

    await expect(exchangeSessionCode('bad-code', 'the-verifier')).rejects.toBeInstanceOf(GatewayError);
    await expect(exchangeSessionCode('bad-code', 'the-verifier')).rejects.toMatchObject({ kind: 'rejected', status: 400 });
  });

  it('throws an `unreachable` GatewayError on a transport failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed: ECONNREFUSED');
      })
    );

    await expect(exchangeSessionCode('any-code', 'the-verifier')).rejects.toMatchObject({ kind: 'unreachable' });
  });

  it('throws `rejected` on a well-formed 200 with a malformed body (missing session_token)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ expires_at: '2026-01-01T00:00:00Z' }), { status: 200 }))
    );

    await expect(exchangeSessionCode('code', 'the-verifier')).rejects.toMatchObject({ kind: 'rejected' });
  });
});

describe('gatewayLogout', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POSTs to /v1/auth/logout with the session token as a Cookie header', async () => {
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      expect(String(url)).toBe('http://localhost:8004/v1/auth/logout');
      expect(init?.method).toBe('POST');
      const headers = (init?.headers ?? {}) as Record<string, string>;
      expect(headers.Cookie).toBe('weave_api_session=sess-xyz');
      return new Response(JSON.stringify({ status: 'ok' }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await gatewayLogout('sess-xyz');

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('never throws, even when Weave-API is unreachable — logging out of this UI must never be blocked by it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed: ECONNREFUSED');
      })
    );

    await expect(gatewayLogout('sess-xyz')).resolves.toBeUndefined();
  });

  it('never throws on a non-2xx response either', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 404 })));

    await expect(gatewayLogout('sess-xyz')).resolves.toBeUndefined();
  });
});

describe('fetchBranding', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const SHA = 'cd'.repeat(32);

  it('GETs /v1/branding without any credential, with a 1.5 s timeout and a 30 s revalidate, and rewrites asset URLs', async () => {
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit & { next?: { revalidate?: number } }) => {
      expect(String(url)).toBe('http://localhost:8004/v1/branding');
      const headers = (init?.headers ?? {}) as Record<string, string>;
      expect(headers.Authorization).toBeUndefined();
      expect(headers.Cookie).toBeUndefined();
      expect(init?.signal).toBeInstanceOf(AbortSignal);
      expect(init?.next?.revalidate).toBe(30);
      return new Response(
        JSON.stringify({ palette: null, logo: { content_type: 'image/png', sha256: SHA, size_bytes: 10, url: '/v1/branding/assets/logo?v=1' }, busy: null }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchBranding()).resolves.toEqual({
      palette: null,
      logo: { contentType: 'image/png', url: `/api/branding/assets/logo?v=${SHA.slice(0, 16)}` },
      busy: null,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['a transport failure', async () => { throw new TypeError('fetch failed'); }],
    ['a timeout', async () => { throw new DOMException('The operation was aborted due to timeout', 'TimeoutError'); }],
    ['a non-2xx answer', async () => new Response('{}', { status: 503 })],
    ['a non-JSON body', async () => new Response('<html>', { status: 200 })],
    ['a non-object body', async () => new Response('[1]', { status: 200 })],
  ])('returns null on %s', async (_label, impl) => {
    vi.stubGlobal('fetch', vi.fn(impl));

    await expect(fetchBranding()).resolves.toBeNull();
  });
});

describe('fetchBrandingAsset', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('forwards only If-None-Match, never a credential', async () => {
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      expect(String(url)).toBe('http://localhost:8004/v1/branding/assets/busy');
      expect(init?.headers).toEqual({ 'If-None-Match': '"abc"' });
      return new Response(null, { status: 304 });
    });
    vi.stubGlobal('fetch', fetchMock);

    expect((await fetchBrandingAsset('busy', '"abc"')).status).toBe(304);
  });

  it('turns a transport failure into GatewayError(unreachable)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));

    await expect(fetchBrandingAsset('logo', null)).rejects.toMatchObject({ name: 'GatewayError', kind: 'unreachable' });
  });
});
