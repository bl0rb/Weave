import { describe, expect, it } from 'vitest';
import {
  classifyHttpStatus,
  errorForHttpStatus,
  errorForInterruptedStream,
  errorForNetworkFailure,
  errorForStreamEvent,
} from '@/lib/errors';

describe('classifyHttpStatus', () => {
  it('maps 401 to auth_expired regardless of context', () => {
    expect(classifyHttpStatus(401)).toBe('auth_expired');
    expect(classifyHttpStatus(401, 'chat')).toBe('auth_expired');
  });

  it('maps 429 to rate_limited', () => {
    expect(classifyHttpStatus(429)).toBe('rate_limited');
  });

  it('maps 422 to validation_error', () => {
    expect(classifyHttpStatus(422)).toBe('validation_error');
  });

  it('maps chat-specific 404/409 only in chat context, generically otherwise', () => {
    expect(classifyHttpStatus(404, 'chat')).toBe('conversation_not_found');
    expect(classifyHttpStatus(409, 'chat')).toBe('conversation_bot_mismatch');
    expect(classifyHttpStatus(404)).toBe('rejected');
    expect(classifyHttpStatus(409)).toBe('rejected');
  });

  it('maps every 5xx to gateway_unreachable', () => {
    expect(classifyHttpStatus(500)).toBe('gateway_unreachable');
    expect(classifyHttpStatus(502)).toBe('gateway_unreachable');
    expect(classifyHttpStatus(503)).toBe('gateway_unreachable');
  });

  it('maps a plain 4xx to rejected', () => {
    expect(classifyHttpStatus(400)).toBe('rejected');
  });
});

describe('mapped messages', () => {
  it('are always German, non-empty, and never simply echo the raw upstream detail', () => {
    const rawDetail = 'Weave-Runtime returned HTTP 503 for POST /internal/chat/stream';
    const mapped = errorForHttpStatus(503, rawDetail);
    expect(mapped.message.length).toBeGreaterThan(0);
    expect(mapped.message).not.toBe(rawDetail);
    // The raw detail is still preserved, just not as the headline message.
    expect(mapped.detail).toBe(rawDetail);
  });

  it('errorForNetworkFailure classifies as gateway_unreachable and carries the failure reason as detail', () => {
    const mapped = errorForNetworkFailure(new TypeError('fetch failed: ECONNREFUSED'));
    expect(mapped.kind).toBe('gateway_unreachable');
    expect(mapped.message).toMatch(/nicht erreichbar/i);
    expect(mapped.detail).toContain('ECONNREFUSED');
  });

  it('errorForNetworkFailure handles a non-Error thrown value without crashing', () => {
    const mapped = errorForNetworkFailure('a plain string rejection');
    expect(mapped.detail).toBe('a plain string rejection');
  });

  it('errorForStreamEvent produces a stream_error with the in-band detail attached', () => {
    const mapped = errorForStreamEvent('LLM provider timed out');
    expect(mapped.kind).toBe('stream_error');
    expect(mapped.detail).toBe('LLM provider timed out');
    expect(mapped.message).not.toContain('LLM provider timed out');
  });

  it('errorForInterruptedStream carries no upstream detail (there was none)', () => {
    const mapped = errorForInterruptedStream();
    expect(mapped.kind).toBe('stream_interrupted');
    expect(mapped.detail).toBeNull();
  });

  it('auth_expired tells the user to log in again', () => {
    const mapped = errorForHttpStatus(401, null);
    expect(mapped.message).toMatch(/melde dich erneut an/i);
  });

  it('conversation_not_found asks the user to resend rather than promising an automatic retry (FINDING 5: chat-app.tsx only clears conversationId, it never resubmits the failed turn itself)', () => {
    const mapped = errorForHttpStatus(404, null, 'chat');
    expect(mapped.kind).toBe('conversation_not_found');
    expect(mapped.message).toMatch(/erneut/i);
    expect(mapped.message).not.toMatch(/wird.*gestartet/i);
  });
});
