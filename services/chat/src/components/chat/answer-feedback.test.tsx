// @vitest-environment jsdom
//
// The feedback row under a finished answer (answer-feedback.tsx) and when
// MessageBubble shows it. See source-cards.test.tsx for why this file opts
// into jsdom per file.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AnswerFeedback } from '@/components/chat/answer-feedback';
import { MessageBubble } from '@/components/chat/message-bubble';
import { mappedError } from '@/lib/errors';
import type { UiMessage } from '@/lib/chat-types';

type FetchMock = ReturnType<typeof vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>>;

function stubFeedbackApi(): FetchMock {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (_input, init) => {
    if (init?.method === 'DELETE') return new Response(null, { status: 204 });
    const body = JSON.parse(init?.body as string);
    return new Response(JSON.stringify({ message_id: 42, rating: body.rating, comment: body.comment ?? null, updated_at: '2026-11-17T10:00:00Z' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const button = (name: string) => screen.getByRole('button', { name });

describe('AnswerFeedback', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders the three ratings as unpressed toggle buttons in a labelled group', () => {
    render(<AnswerFeedback messageId={42} feedback={null} />);
    expect(screen.getByRole('group', { name: 'Wie hilfreich war diese Antwort?' })).toBeTruthy();
    for (const name of ['Hilfreich', 'Falsch', 'Prüfung anfordern']) expect(button(name).getAttribute('aria-pressed')).toBe('false');
    expect(screen.queryByLabelText('Was ist falsch oder soll geprüft werden? (optional)')).toBeNull();
  });

  it('saves „Hilfreich“ right away, marks it pressed and thanks the user', async () => {
    const fetchMock = stubFeedbackApi();
    const onChange = vi.fn();
    render(<AnswerFeedback messageId={42} feedback={null} onChange={onChange} />);

    fireEvent.click(button('Hilfreich'));

    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Danke für die Rückmeldung');
    expect(button('Hilfreich').getAttribute('aria-pressed')).toBe('true');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/messages/42/feedback');
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(init?.body as string)).toEqual({ rating: 'helpful', comment: null });
    expect(onChange).toHaveBeenCalledWith({ rating: 'helpful', comment: null, updated_at: '2026-11-17T10:00:00Z' });
    // No comment field for a positive rating.
    expect(screen.queryByLabelText('Was ist falsch oder soll geprüft werden? (optional)')).toBeNull();
  });

  it('withdraws the rating (DELETE) when the active rating is clicked again', async () => {
    const fetchMock = stubFeedbackApi();
    const onChange = vi.fn();
    render(<AnswerFeedback messageId={42} feedback={{ rating: 'helpful', comment: null, updated_at: 'x' }} onChange={onChange} />);
    expect(button('Hilfreich').getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(button('Hilfreich'));

    await waitFor(() => expect(button('Hilfreich').getAttribute('aria-pressed')).toBe('false'));
    expect(fetchMock.mock.calls[0][1]?.method).toBe('DELETE');
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/messages/42/feedback');
    expect(onChange).toHaveBeenCalledWith(null);
    expect(screen.getByRole('status').textContent).toBe('Rückmeldung zurückgenommen');
  });

  it('stores „Falsch“ at once, then offers an optional comment that re-saves the same rating with it', async () => {
    const fetchMock = stubFeedbackApi();
    render(<AnswerFeedback messageId={42} feedback={null} />);

    fireEvent.click(button('Falsch'));
    const field = (await screen.findByLabelText('Was ist falsch oder soll geprüft werden? (optional)')) as HTMLTextAreaElement;
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({ rating: 'wrong', comment: null });
    expect(field.maxLength).toBe(2000);

    fireEvent.change(field, { target: { value: 'Die Frist beträgt 14 Tage.' } });
    fireEvent.click(button('Kommentar senden'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({ rating: 'wrong', comment: 'Die Frist beträgt 14 Tage.' });
    await waitFor(() => expect(screen.queryByLabelText('Was ist falsch oder soll geprüft werden? (optional)')).toBeNull());
    expect(button('Falsch').getAttribute('aria-pressed')).toBe('true');
  });

  it('switches from one rating to another with a single PUT, keeping a typed comment for the other negative rating', async () => {
    const fetchMock = stubFeedbackApi();
    render(<AnswerFeedback messageId={42} feedback={null} />);
    fireEvent.click(button('Falsch'));
    fireEvent.change(await screen.findByLabelText('Was ist falsch oder soll geprüft werden? (optional)'), { target: { value: 'alt' } });

    fireEvent.click(button('Prüfung anfordern'));

    await waitFor(() => expect(button('Prüfung anfordern').getAttribute('aria-pressed')).toBe('true'));
    expect(button('Falsch').getAttribute('aria-pressed')).toBe('false');
    expect(fetchMock.mock.calls.map(([, init]) => init?.method)).toEqual(['PUT', 'PUT']);
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({ rating: 'outdated', comment: 'alt' });
  });

  it('lets a stored negative rating get a comment later, prefilled with the stored one', async () => {
    stubFeedbackApi();
    render(<AnswerFeedback messageId={42} feedback={{ rating: 'outdated', comment: 'Stand 2024', updated_at: 'x' }} />);
    expect(button('Prüfung anfordern').getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByLabelText('Was ist falsch oder soll geprüft werden? (optional)')).toBeNull();

    fireEvent.click(button('Kommentar ergänzen'));

    expect((screen.getByLabelText('Was ist falsch oder soll geprüft werden? (optional)') as HTMLTextAreaElement).value).toBe('Stand 2024');
  });

  it('keeps the previous state and shows an error when saving fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(mappedError('gateway_unreachable')), { status: 502 })));
    const onChange = vi.fn();
    render(<AnswerFeedback messageId={42} feedback={null} onChange={onChange} />);

    fireEvent.click(button('Hilfreich'));

    expect((await screen.findByRole('alert')).textContent).toBe('Die Rückmeldung konnte nicht gespeichert werden. Bitte versuche es erneut.');
    expect(button('Hilfreich').getAttribute('aria-pressed')).toBe('false');
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('MessageBubble feedback row', () => {
  afterEach(() => cleanup());

  function answer(overrides: Partial<UiMessage> = {}): UiMessage {
    return {
      id: 'ui-1', role: 'assistant', content: 'Antwort', streaming: false, sources: null, trace: null, error: null,
      viaFallback: false, slowResponse: false, progressLine: null, agentStatuses: [], serverId: 42, ...overrides,
    };
  }

  it('is shown under a finished, persisted answer, with the stored rating pressed', () => {
    render(<MessageBubble message={answer({ feedback: { rating: 'wrong', comment: null, updated_at: 'x' } })} />);
    expect(screen.getByRole('group', { name: 'Wie hilfreich war diese Antwort?' })).toBeTruthy();
    expect(button('Falsch').getAttribute('aria-pressed')).toBe('true');
  });

  it.each<[string, Partial<UiMessage>]>([
    ['while streaming', { streaming: true }],
    ['after an error', { error: mappedError('stream_interrupted') }],
    ['before Weave-API named the message id', { serverId: undefined }],
  ])('is not shown %s', (_label, overrides) => {
    render(<MessageBubble message={answer(overrides)} />);
    expect(screen.queryByRole('group', { name: 'Wie hilfreich war diese Antwort?' })).toBeNull();
  });

  it('is never shown under a user message', () => {
    render(<MessageBubble message={answer({ role: 'user' })} />);
    expect(screen.queryByRole('button', { name: 'Hilfreich' })).toBeNull();
  });
});
