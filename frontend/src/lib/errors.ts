/**
 * Central place that turns "something went wrong talking to Weave-API"
 * into one understandable German sentence — never a raw status code, a
 * stack trace, or an upstream `detail` string shown as if it were meant
 * for an end user. Pure and isomorphic (no fetch, no cookies) so it is
 * usable from Route Handlers, client components, and plain unit tests
 * alike.
 *
 * The raw `detail` (Weave-API's own `HTTPException.detail`, or an
 * in-stream `error` event's `detail`) is still carried on `MappedError`
 * for anyone building a developer-facing view (this UI's Trace panel) —
 * it is just never interpolated into `message` itself.
 */

export type ErrorKind =
  | 'invalid_token'
  | 'auth_expired'
  | 'gateway_unreachable'
  | 'rate_limited'
  | 'conversation_not_found'
  | 'conversation_bot_mismatch'
  | 'validation_error'
  | 'rejected'
  | 'stream_interrupted'
  | 'stream_error'
  | 'unknown';

export interface MappedError {
  kind: ErrorKind;
  /** Always German, always safe to render directly to the end user. */
  message: string;
  /** Raw upstream detail, if any — for a developer/trace view only. */
  detail: string | null;
}

const MESSAGES: Record<ErrorKind, string> = {
  invalid_token: 'Das Token wurde nicht akzeptiert. Bitte prüfe es und versuche es erneut.',
  auth_expired: 'Deine Anmeldung ist abgelaufen oder ungültig. Bitte melde dich erneut an.',
  gateway_unreachable: 'Weave-API ist gerade nicht erreichbar. Bitte versuche es in Kürze erneut.',
  rate_limited: 'Zu viele Anfragen kurz hintereinander. Bitte warte einen Moment und versuche es erneut.',
  // FINDING 5: this used to promise "Es wird stattdessen eine neue
  // Konversation gestartet." — chat-app.tsx only clears `conversationId`
  // (so the NEXT turn starts fresh) and never resends the failed turn
  // itself. An automatic one-shot retry was considered instead of fixing
  // the wording, but rejected: it would have to either silently resend the
  // user's exact wording without giving them a chance to review it first
  // (the request/context that made this conversation_id stale could just
  // as easily have made the message itself stale), or risk being
  // confusing about which of two now-visible user turns is "the" one that
  // actually went through. Neither is worth it just to keep a promise the
  // text didn't need to make — the honest, simpler fix is to describe what
  // actually happens.
  conversation_not_found: 'Diese Konversation wurde nicht gefunden. Bitte sende deine Nachricht erneut.',
  conversation_bot_mismatch:
    'Diese Konversation gehört zu einem anderen Bot. Bitte wähle den ursprünglichen Bot oder starte eine neue Konversation.',
  validation_error: 'Die Anfrage war ungültig und wurde nicht gesendet.',
  rejected: 'Die Anfrage wurde vom Gateway abgelehnt.',
  stream_interrupted: 'Die Verbindung wurde während der Antwort unterbrochen. Bitte versuche es erneut.',
  stream_error: 'Bei der Erzeugung der Antwort ist ein Fehler aufgetreten. Bitte versuche es erneut.',
  unknown: 'Es ist ein unerwarteter Fehler aufgetreten.',
};

export function mappedError(kind: ErrorKind, detail: string | null = null): MappedError {
  return { kind, message: MESSAGES[kind], detail };
}

/**
 * Classifies an HTTP status Weave-API answered with into an `ErrorKind`.
 * `context: 'chat'` additionally distinguishes the two documented 4xx
 * outcomes specific to POST /v1/chat(/stream) — a plain conversation_id
 * (404, `ConversationNotFound`) vs. one that belongs to a different bot
 * (409, `ConversationBotMismatch`) — see Weave-API backend/app/api/chat.py.
 * Every other route only ever needs the generic classification.
 */
export function classifyHttpStatus(status: number, context?: 'chat'): ErrorKind {
  if (status === 401) return 'auth_expired';
  if (status === 429) return 'rate_limited';
  if (status === 422) return 'validation_error';
  if (context === 'chat' && status === 404) return 'conversation_not_found';
  if (context === 'chat' && status === 409) return 'conversation_bot_mismatch';
  if (status >= 500) return 'gateway_unreachable';
  if (status >= 400) return 'rejected';
  return 'unknown';
}

export function errorForHttpStatus(status: number, detail: string | null, context?: 'chat'): MappedError {
  return mappedError(classifyHttpStatus(status, context), detail);
}

/** A `fetch()` to Weave-API itself threw (DNS failure, connection refused,
 * timeout, TLS error, ...) — there was no HTTP response at all. */
export function errorForNetworkFailure(cause: unknown): MappedError {
  const detail = cause instanceof Error ? cause.message : String(cause);
  return mappedError('gateway_unreachable', detail);
}

/** An in-band `{"type": "error", "detail": ...}` SSE event — the stream's
 * `200 OK` was already committed, so this is the only way Weave-Runtime's
 * own mid-generation failure can still reach a caller (see
 * contracts/internal-chat.md's "Fehlerverhalten"). */
export function errorForStreamEvent(detail: string): MappedError {
  return mappedError('stream_error', detail);
}

/** The stream's underlying connection ended with neither a `done` nor an
 * `error` event ever observed — a dropped connection, not a clean finish
 * and not a reported failure either. */
export function errorForInterruptedStream(): MappedError {
  return mappedError('stream_interrupted', null);
}
