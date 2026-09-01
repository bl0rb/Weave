import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { setSessionCookie } from '@/lib/session';
import { weaveApiFetch } from '@/lib/weave-api-server';
import { errorForNetworkFailure, errorForHttpStatus, mappedError } from '@/lib/errors';
import type { Bot } from '@/types/weave-api';

interface LoginBody {
  token?: unknown;
}

/**
 * POST /api/session/login — takes the token the user typed into the
 * /login form, PROVES it works by calling GET /v1/bots with it (the
 * cheapest authenticated Weave-API route available), and only then sets
 * the httpOnly session cookie. A token that Weave-API rejects never
 * touches a cookie at all — see src/lib/session.ts / weave-api-server.ts
 * for why the token lives server-side in the first place.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  let body: LoginBody;
  try {
    body = (await request.json()) as LoginBody;
  } catch {
    return NextResponse.json(mappedError('validation_error', null), { status: 400 });
  }

  const token = typeof body.token === 'string' ? body.token.trim() : '';
  if (!token) {
    return NextResponse.json(
      { ...mappedError('validation_error', null), message: 'Bitte gib ein Token ein.' },
      { status: 400 }
    );
  }

  try {
    const response = await weaveApiFetch('/v1/bots', { kind: 'bearer', value: token });

    if (response.status === 401) {
      return NextResponse.json(mappedError('invalid_token', null), { status: 401 });
    }
    if (!response.ok) {
      return NextResponse.json(errorForHttpStatus(response.status, null), { status: 502 });
    }

    // Body is otherwise unused here — this call exists purely to validate
    // the token, not to relay a bot list from the login form. Reading it
    // still confirms Weave-API answered a well-formed 200, not e.g. an
    // HTML error page from a misconfigured reverse proxy.
    await response.json().catch(() => null as Bot[] | null);

    const ok = NextResponse.json({ ok: true });
    setSessionCookie(ok, token, { kind: 'bearer' });
    return ok;
  } catch (cause) {
    // weaveApiFetch only ever throws for a transport-level failure (no
    // HTTP response at all) — see its own docstring.
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
