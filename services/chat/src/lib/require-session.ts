import 'server-only';
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { getSessionCredential, type SessionCredential } from '@/lib/session';
import { mappedError } from '@/lib/errors';

/** Shared guard for every proxy Route Handler: no session cookie means no
 * point even calling Weave-API — respond 401 straight away, without
 * making a network call, using the same 'auth_expired' shape the UI
 * already knows how to render as "please log in again". The returned
 * `credential` carries its own `kind` ('bearer' for a Personal-API-Token,
 * 'session' for one obtained via the SSO handoff) — pass it straight to
 * weave-api-server.ts's `weaveApiFetch`/`weaveApiFetchJson`, which is the
 * one place that turns it into the right outgoing header. */
export function requireSessionCredential(request: NextRequest): { credential: SessionCredential } | { response: NextResponse } {
  const credential = getSessionCredential(request);
  if (!credential) {
    return { response: NextResponse.json(mappedError('auth_expired', null), { status: 401 }) };
  }
  return { credential };
}
