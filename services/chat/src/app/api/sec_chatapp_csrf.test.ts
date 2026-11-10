import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { POST as login } from '@/app/api/session/login/route';
import { POST as logout } from '@/app/api/session/logout/route';
import { PUT as locale } from '@/app/api/session/locale/route';
import { POST as chat } from '@/app/api/chat/route';
import { POST as chatStream } from '@/app/api/chat/stream/route';
import { DELETE as deleteConversations } from '@/app/api/conversations/route';
import { DELETE as deleteConversation } from '@/app/api/conversations/[id]/route';
import { POST as createToken } from '@/app/api/tokens/route';
import { DELETE as deleteToken } from '@/app/api/tokens/[id]/route';
import { SESSION_COOKIE_NAME } from '@/lib/session';

type Handler = (request: NextRequest) => Promise<Response>;

const idParams = { params: Promise.resolve({ id: 'x' }) };

// Every state-changing Route Handler under src/app/api, with whether it
// parses a JSON body.
const ROUTES: Array<[string, string, Handler, boolean]> = [
  ['POST', '/api/session/login', login, true],
  ['POST', '/api/session/logout', logout, false],
  ['PUT', '/api/session/locale', locale, true],
  ['POST', '/api/chat', chat, true],
  ['POST', '/api/chat/stream', chatStream, true],
  ['DELETE', '/api/conversations', deleteConversations, false],
  ['DELETE', '/api/conversations/x', req => deleteConversation(req, idParams), false],
  ['POST', '/api/tokens', createToken, true],
  ['DELETE', '/api/tokens/x', req => deleteToken(req, idParams), false],
];

function request(method: string, path: string, headers: Record<string, string>, json = true): NextRequest {
  return new NextRequest(`http://chat.internal:3000${path}`, {
    method,
    headers: {
      cookie: `${SESSION_COOKIE_NAME}=wt_victim`,
      ...(json && method !== 'DELETE' ? { 'content-type': 'application/json' } : {}),
      ...headers,
    },
    body: method === 'DELETE' ? undefined : JSON.stringify({ token: 'wt_attacker', locale: 'de', label: 'x' }),
  });
}

describe('CSRF guard on every state-changing /api route', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it.each(ROUTES)('%s %s: rejects a foreign Origin with 403 before touching Weave-API or cookies', async (method, path, handler) => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await handler(request(method, path, { origin: 'https://evil.example' }));

    expect(res.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it.each(ROUTES)('%s %s: rejects Origin: null and a cross-site Sec-Fetch-Site', async (method, path, handler) => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    expect((await handler(request(method, path, { origin: 'null' }))).status).toBe(403);
    expect((await handler(request(method, path, { 'sec-fetch-site': 'cross-site' }))).status).toBe(403);
    expect((await handler(request(method, path, { 'sec-fetch-site': 'same-site' }))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(ROUTES.filter(route => route[3]))(
    '%s %s: requires Content-Type application/json (a cross-site form can only send text/plain)',
    async (method, path, handler) => {
      const fetchMock = vi.fn();
      vi.stubGlobal('fetch', fetchMock);

      const res = await handler(request(method, path, { 'content-type': 'text/plain' }));

      expect(res.status).toBe(415);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(res.headers.get('set-cookie')).toBeNull();
    },
  );

  it('login: a cross-site text/plain form post without Origin still never sets a cookie', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const res = await login(request('POST', '/api/session/login', { 'content-type': 'text/plain;charset=UTF-8' }, false));

    expect(res.status).toBe(415);
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  describe('same-origin requests keep working', () => {
    function acceptAll() {
      const fetchMock = vi.fn(async () => new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } }));
      vi.stubGlobal('fetch', fetchMock);
      return fetchMock;
    }

    it('Origin equal to APP_BASE_URL (behind a proxy, request.url is the internal host)', async () => {
      vi.stubEnv('APP_BASE_URL', 'https://chat.example.com');
      const fetchMock = acceptAll();

      const res = await login(request('POST', '/api/session/login', { origin: 'https://chat.example.com' }));

      expect(res.status).toBe(200);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(res.headers.get('set-cookie')).toContain(SESSION_COOKIE_NAME);
    });

    it('Origin equal to the Host the browser addressed (deployment reached at another address than APP_BASE_URL)', async () => {
      vi.stubEnv('APP_BASE_URL', 'http://localhost:3001');
      acceptAll();

      const res = await login(
        request('POST', '/api/session/login', { origin: 'http://192.168.1.20:3001', host: '192.168.1.20:3001' }),
      );

      expect(res.status).toBe(200);
    });

    it('Origin equal to X-Forwarded-Proto/-Host set by the reverse proxy', async () => {
      acceptAll();

      const res = await login(
        request('POST', '/api/session/login', {
          origin: 'https://weave.example.org',
          'x-forwarded-proto': 'https',
          'x-forwarded-host': 'weave.example.org',
        }),
      );

      expect(res.status).toBe(200);
    });

    it('no Origin but Sec-Fetch-Site: same-origin', async () => {
      acceptAll();
      const res = await logout(request('POST', '/api/session/logout', { 'sec-fetch-site': 'same-origin' }));
      expect(res.status).toBe(200);
    });

    it('neither header (not a browser, so not a forgery) — e.g. a script holding the cookie', async () => {
      acceptAll();
      const res = await logout(request('POST', '/api/session/logout', {}));
      expect(res.status).toBe(200);
    });
  });
});
