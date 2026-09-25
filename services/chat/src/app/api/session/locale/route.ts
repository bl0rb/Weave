import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { requireSessionCredential } from '@/lib/require-session';
import { GatewayError, weaveApiFetchJson } from '@/lib/weave-api-server';
import { errorForHttpStatus, errorForNetworkFailure, mappedError } from '@/lib/errors';
import { isLocale, type Locale } from '@/i18n/config';

interface LocaleBody {
  locale?: unknown;
}

/**
 * PUT /api/session/locale — proxies Weave-API's PUT /v1/me/locale:
 * persists the signed-in user's language choice to their account, called
 * by <LanguageSwitch/> right after it already applied the choice locally
 * (see i18n/language-switch.tsx). Unlike the flat 401/502 squash every
 * other proxy in this app uses, an upstream failure's exact status —
 * in particular Weave-API's documented 503 when its account store is
 * unreachable — is passed straight through instead of being folded into
 * a generic 502: the caller here already decided to keep the new
 * language for this browser regardless of what this call answers, so
 * there is nothing gained from hiding which kind of failure it was.
 */
export async function PUT(request: NextRequest): Promise<NextResponse> {
  const session = requireSessionCredential(request);
  if ('response' in session) return session.response;

  let body: LocaleBody;
  try {
    body = (await request.json()) as LocaleBody;
  } catch {
    return NextResponse.json(mappedError('validation_error', null), { status: 400 });
  }

  if (!isLocale(body.locale)) {
    return NextResponse.json(mappedError('validation_error', null), { status: 400 });
  }

  try {
    const result = await weaveApiFetchJson<{ locale: Locale }>('/v1/me/locale', session.credential, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ locale: body.locale }),
    });
    return NextResponse.json(result);
  } catch (cause) {
    if (cause instanceof GatewayError) {
      const status = cause.status ?? 502;
      return NextResponse.json(errorForHttpStatus(status, cause.detail), { status });
    }
    return NextResponse.json(errorForNetworkFailure(cause), { status: 502 });
  }
}
