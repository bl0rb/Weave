import { errorForNetworkFailure, mappedError, type MappedError } from '@/lib/errors';

export type JsonResult<T> = { ok: true; data: T } | { ok: false; error: MappedError };

/** Fetches one of THIS app's own `/api/*` Route Handlers (never Weave-API
 * directly — see src/lib/weave-api-server.ts) and normalizes both
 * failure shapes a client component has to handle: a network failure
 * reaching our own server, and a non-2xx JSON response, which every
 * Route Handler in this app already shapes as a `MappedError` (see
 * src/lib/errors.ts). */
export async function getJson<T>(path: string): Promise<JsonResult<T>> {
  let response: Response;
  try {
    response = await fetch(path, { cache: 'no-store' });
  } catch (cause) {
    return { ok: false, error: errorForNetworkFailure(cause) };
  }
  return toJsonResult<T>(response);
}

export async function postJson<T>(path: string, body: unknown): Promise<JsonResult<T>> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    return { ok: false, error: errorForNetworkFailure(cause) };
  }
  return toJsonResult<T>(response);
}

async function toJsonResult<T>(response: Response): Promise<JsonResult<T>> {
  if (response.ok) {
    return { ok: true, data: (await response.json()) as T };
  }
  const parsed = (await response.json().catch(() => null)) as MappedError | null;
  return { ok: false, error: parsed ?? mappedError('unknown', null) };
}
