import 'server-only';
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { getSessionToken } from '@/lib/session';
import { mappedError } from '@/lib/errors';

/** Shared guard for every proxy Route Handler: no session cookie means no
 * point even calling Weave-API — respond 401 straight away, without
 * making a network call, using the same 'auth_expired' shape the UI
 * already knows how to render as "please log in again". */
export function requireSessionToken(request: NextRequest): { token: string } | { response: NextResponse } {
  const token = getSessionToken(request);
  if (!token) {
    return { response: NextResponse.json(mappedError('auth_expired', null), { status: 401 }) };
  }
  return { token };
}
