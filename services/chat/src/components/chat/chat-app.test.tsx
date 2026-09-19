// @vitest-environment jsdom
//
// A deliberate, scoped exception to this suite's otherwise jsdom-free setup
// (see vitest.config.ts's own docstring): the regression this guards
// against — chat-app.tsx's `sending` flag surviving a turn the user has
// since abandoned — is a real interaction between two event handlers and an
// in-flight `fetch`, not something a pure-function unit test could exercise
// without first pulling the turn-lifecycle state out of the component,
// which is a bigger refactor than this bug fix warrants. The
// `// @vitest-environment jsdom` pragma above opts ONLY this file into a DOM
// environment via React Testing Library; every other test in this suite
// still runs in plain Node.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ChatApp } from '@/components/chat/chat-app';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

const BOTS = [
  { id: 'bot-a', name: 'Bot A', description: null, retrieval: { enabled: false } },
  { id: 'bot-b', name: 'Bot B', description: null, retrieval: { enabled: false } },
];

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

describe('ChatApp turn lifecycle', () => {
  beforeEach(() => {
    // jsdom does not implement matchMedia; ThemeToggle (rendered inside
    // Sidebar) reads it on mount to pick a default theme.
    window.matchMedia =
      window.matchMedia ??
      ((() => ({
        matches: false,
        media: '',
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      })) as unknown as typeof window.matchMedia);

    // jsdom does not implement scrollIntoView either; MessageList calls it
    // on every message-list change to keep the transcript scrolled down.
    Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('re-enables the composer after switching bots mid-turn, instead of leaving it locked forever', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>((input) => {
      const url = String(input);
      if (url.endsWith('/api/bots')) return Promise.resolve(jsonResponse(BOTS));
      if (url.endsWith('/api/collections')) return Promise.resolve(jsonResponse([]));
      if (url.endsWith('/api/chat/stream')) {
        // Simulates a turn that never finishes -- the only code path that
        // can still clear `sending` is the fix under test (handleSelectBot
        // resetting it directly), never handleSend's own `finally` block,
        // since that block's guard (`activeTurnRef.current === turnId`)
        // will never match again once the turn is abandoned.
        return new Promise<Response>(() => {});
      }
      throw new Error(`unexpected fetch to ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ChatApp />);

    await screen.findByRole('button', { name: /Bot A/ });

    const textarea = (await screen.findByPlaceholderText('Nachricht schreiben…')) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Erste Frage' } });
    fireEvent.click(screen.getByRole('button', { name: 'Nachricht senden' }));

    // The turn is now in flight -- the composer must be locked, matching
    // the component's own `disabled={sending}`.
    await waitFor(() => expect(textarea.disabled).toBe(true));

    // Abandon it: switch to the other bot while the first turn is still
    // pending, exactly the sequence FINDING 1 describes.
    fireEvent.click(screen.getByRole('button', { name: /Bot B/ }));

    await waitFor(() => expect(textarea.disabled).toBe(false));

    // And sending must genuinely work again, not just look unlocked.
    fireEvent.change(textarea, { target: { value: 'Zweite Frage' } });
    fireEvent.click(screen.getByRole('button', { name: 'Nachricht senden' }));

    await waitFor(() => {
      const streamCalls = fetchMock.mock.calls.filter(([req]) => String(req).endsWith('/api/chat/stream'));
      expect(streamCalls.length).toBe(2);
    });
  });

  it('shows the still-working indicator after 30s of silence mid-stream, even once content has already arrived', async () => {
    // Regression test for the gap where armSlowTimer's 30s idle check was
    // only ever surfaced by message-bubble.tsx's empty-content branch — so
    // it silently stopped showing anything the moment the first delta of
    // an incrementally-streamed answer (the actual long-running n8n-agent
    // case) arrived. Uses fake timers to advance past the 30s threshold
    // without a real wait, and a ReadableStream that emits one delta and
    // then simply never closes, exactly like a live n8n run mid tool-call.
    // `shouldAdvanceTime` lets the fake clock tick forward alongside real
    // wall-clock time (so `vi.waitFor`'s own polling still progresses),
    // while `vi.advanceTimersByTimeAsync` below still lets us jump the 30s
    // idle threshold instantly instead of actually waiting for it.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>((input) => {
        const url = String(input);
        if (url.endsWith('/api/bots')) return Promise.resolve(jsonResponse([BOTS[0]]));
        if (url.endsWith('/api/collections')) return Promise.resolve(jsonResponse([]));
        if (url.endsWith('/api/chat/stream')) {
          const encoder = new TextEncoder();
          const stream = new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode('data: {"type":"delta","text":"Hallo"}\n\n'));
              // Deliberately never closed/enqueued again -- simulates a
              // multi-minute gap between tool-call deltas.
            },
          });
          return Promise.resolve(
            new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
          );
        }
        throw new Error(`unexpected fetch to ${url}`);
      });
      vi.stubGlobal('fetch', fetchMock);

      render(<ChatApp />);

      const textarea = (await screen.findByPlaceholderText('Nachricht schreiben…')) as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: 'Erste Frage' } });
      fireEvent.click(screen.getByRole('button', { name: 'Nachricht senden' }));

      await vi.waitFor(() => expect(screen.getByText('Hallo')).toBeTruthy());

      // Not yet -- fewer than 30s of silence have passed since the delta.
      expect(screen.queryByText('Der Assistent arbeitet noch …')).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      expect(
        screen.getByText((_, element) => element?.textContent === 'Der Assistent arbeitet noch …')
      ).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('re-enables the composer after starting a new conversation mid-turn', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>((input) => {
      const url = String(input);
      if (url.endsWith('/api/bots')) return Promise.resolve(jsonResponse([BOTS[0]]));
      if (url.endsWith('/api/collections')) return Promise.resolve(jsonResponse([]));
      if (url.endsWith('/api/chat/stream')) return new Promise<Response>(() => {});
      throw new Error(`unexpected fetch to ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ChatApp />);

    const textarea = (await screen.findByPlaceholderText('Nachricht schreiben…')) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Erste Frage' } });
    fireEvent.click(screen.getByRole('button', { name: 'Nachricht senden' }));
    await waitFor(() => expect(textarea.disabled).toBe(true));

    // "Neue Konversation" only becomes clickable once there is at least
    // one message -- handleSend already added the user + pending
    // assistant message by this point.
    fireEvent.click(screen.getByRole('button', { name: /Neue Konversation/ }));

    await waitFor(() => expect(textarea.disabled).toBe(false));
  });
});

const COLLECTIONS = [
  { slug: 'legal-2026', name: 'Legal 2026', description: null, public: false },
  { slug: 'hr-docs', name: 'HR Docs', description: null, public: false },
];

describe('ChatApp collection filter', () => {
  beforeEach(() => {
    window.matchMedia =
      window.matchMedia ??
      ((() => ({
        matches: false,
        media: '',
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      })) as unknown as typeof window.matchMedia);
    Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function mockFetch() {
    return vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>((input) => {
      const url = String(input);
      if (url.endsWith('/api/bots')) return Promise.resolve(jsonResponse(BOTS));
      if (url.endsWith('/api/collections')) return Promise.resolve(jsonResponse(COLLECTIONS));
      // The turn's outcome is irrelevant to these tests — only the request
      // body sent to /api/chat/stream is under test — so it is left
      // pending forever, exactly like the lifecycle tests above.
      if (url.endsWith('/api/chat/stream')) return new Promise<Response>(() => {});
      throw new Error(`unexpected fetch to ${url}`);
    });
  }

  async function sendMessage(fetchMock: ReturnType<typeof mockFetch>, text: string) {
    const textarea = (await screen.findByPlaceholderText('Nachricht schreiben…')) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: text } });
    fireEvent.click(screen.getByRole('button', { name: 'Nachricht senden' }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([req]) => String(req).endsWith('/api/chat/stream'))).toBe(true);
    });
    const [, init] = fetchMock.mock.calls.find(([req]) => String(req).endsWith('/api/chat/stream'))!;
    return JSON.parse(init!.body as string);
  }

  it('sends the sidebar collection selection as the request filter', async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal('fetch', fetchMock);

    render(<ChatApp />);
    await screen.findByRole('button', { name: /Bot A/ });
    fireEvent.click(screen.getByRole('button', { name: /Legal 2026/ }));

    const body = await sendMessage(fetchMock, 'Was gilt hier?');
    expect(body.collections).toEqual(['legal-2026']);
  });

  it('sends no collections field at all when the selection is empty', async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal('fetch', fetchMock);

    render(<ChatApp />);
    await screen.findByRole('button', { name: /Bot A/ });

    const body = await sendMessage(fetchMock, 'Was gilt hier?');
    expect(body).not.toHaveProperty('collections');
  });
});
