// @vitest-environment jsdom
//
// The left rail absorbed the former separate right-hand HistoryPanel — the
// bulk of these are that component's own former tests, now against Rail,
// plus the grouped-history pure function and the off-canvas drawer state
// that only exist here. See vitest.config.ts's own docstring for why this
// file opts into jsdom via a per-file pragma while the rest of the suite
// stays plain Node.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Rail, groupConversations } from '@/components/chat/rail';
import type { ConversationSummary } from '@/types/weave-api';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: () => {}, refresh: () => {} }),
}));

beforeEach(() => {
  // jsdom does not implement matchMedia; ThemeToggle (rendered in the
  // rail's own footer) reads it on mount to pick a default theme.
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
});

afterEach(() => cleanup());

function conversation(overrides: Partial<ConversationSummary>): ConversationSummary {
  return { id: 'c', bot_id: 'faq-bot', title: 'T', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', ...overrides };
}

describe('groupConversations', () => {
  // Offsets from a fixed instant rather than separate literal timestamps
  // near a calendar-day boundary — a fixed DURATION shift preserves the
  // same local time-of-day (so the same day-count difference) in every
  // timezone the test happens to run under, which literal "just after
  // midnight" style timestamps would not.
  const now = new Date('2026-01-08T12:00:00Z');
  const hoursAgo = (h: number) => new Date(now.getTime() - h * 60 * 60 * 1000).toISOString();

  it('buckets a conversation updated minutes ago as "Heute"', () => {
    const today = conversation({ id: 'today', updated_at: hoursAgo(0.5) });
    expect(groupConversations([today], now)).toEqual([['Heute', [today]]]);
  });

  it('buckets a conversation updated 3 days ago as "Diese Woche"', () => {
    const thisWeek = conversation({ id: 'this-week', updated_at: hoursAgo(3 * 24) });
    expect(groupConversations([thisWeek], now)).toEqual([['Diese Woche', [thisWeek]]]);
  });

  it('buckets a conversation updated 60 days ago as "Älter"', () => {
    const old = conversation({ id: 'old', updated_at: hoursAgo(60 * 24) });
    expect(groupConversations([old], now)).toEqual([['Älter', [old]]]);
  });

  it('omits a group entirely when it has no entries, preserving Heute/Diese Woche/Älter order for the ones that do', () => {
    const today = conversation({ id: 'today', updated_at: hoursAgo(0.5) });
    const old = conversation({ id: 'old', updated_at: hoursAgo(60 * 24) });
    expect(groupConversations([old, today], now).map(([label]) => label)).toEqual(['Heute', 'Älter']);
  });
});

const CONVERSATIONS: ConversationSummary[] = [
  conversation({ id: 'conv-1', title: 'VPN-Frage', updated_at: '2026-01-08T09:00:00Z' }),
  conversation({ id: 'conv-2', title: null, updated_at: '2026-01-08T08:00:00Z' }),
];

function baseProps() {
  return {
    conversations: CONVERSATIONS,
    conversationsError: null,
    selectedConversationId: null,
    onSelectConversation: vi.fn(),
    onDeleteConversation: vi.fn(),
    onDeleteAllConversations: vi.fn(),
    onNewConversation: vi.fn(),
    newConversationDisabled: false,
    open: false,
    onClose: vi.fn(),
  };
}

describe('Rail', () => {
  it('shows a loading placeholder, then the titled entries once loaded', () => {
    const { rerender } = render(<Rail {...baseProps()} conversations={null} />);
    expect(screen.getByText('Verlauf wird geladen…')).toBeTruthy();

    rerender(<Rail {...baseProps()} />);
    expect(screen.getByText('VPN-Frage')).toBeTruthy();
    expect(screen.getByText('Ohne Titel')).toBeTruthy();
  });

  it('shows "no conversations" once loaded empty', () => {
    render(<Rail {...baseProps()} conversations={[]} />);
    expect(screen.getByText('Noch keine gespeicherten Konversationen.')).toBeTruthy();
  });

  it('calls onSelectConversation when an entry is clicked, and marks the active one as pressed', () => {
    const onSelectConversation = vi.fn();
    render(<Rail {...baseProps()} selectedConversationId="conv-2" onSelectConversation={onSelectConversation} />);

    const active = screen.getByRole('button', { name: 'Ohne Titel' });
    expect(active.getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'VPN-Frage' }));
    expect(onSelectConversation).toHaveBeenCalledWith('conv-1');
  });

  it('calls onDeleteConversation with the entry id when its delete button is clicked', () => {
    const onDeleteConversation = vi.fn();
    render(<Rail {...baseProps()} onDeleteConversation={onDeleteConversation} />);

    fireEvent.click(screen.getAllByRole('button', { name: 'Konversation löschen' })[0]);
    expect(onDeleteConversation).toHaveBeenCalledWith('conv-1');
  });

  it('offers deleting the whole history only while entries exist', () => {
    const onDeleteAllConversations = vi.fn();
    const { rerender } = render(<Rail {...baseProps()} conversations={[]} onDeleteAllConversations={onDeleteAllConversations} />);
    expect(screen.queryByRole('button', { name: 'Verlauf löschen' })).toBeNull();

    rerender(<Rail {...baseProps()} onDeleteAllConversations={onDeleteAllConversations} />);
    fireEvent.click(screen.getByRole('button', { name: 'Verlauf löschen' }));
    expect(onDeleteAllConversations).toHaveBeenCalledOnce();
  });

  it('calls onNewConversation from the "Neues Gespräch" button, respecting the disabled flag', () => {
    const onNewConversation = vi.fn();
    const { rerender } = render(<Rail {...baseProps()} onNewConversation={onNewConversation} newConversationDisabled />);
    expect((screen.getByRole('button', { name: 'Neues Gespräch' }) as HTMLButtonElement).disabled).toBe(true);

    rerender(<Rail {...baseProps()} onNewConversation={onNewConversation} newConversationDisabled={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Neues Gespräch' }));
    expect(onNewConversation).toHaveBeenCalledOnce();
  });

  describe('drawer (below the 900px breakpoint)', () => {
    it('calls onClose on Escape only while open', () => {
      const onClose = vi.fn();
      const { rerender } = render(<Rail {...baseProps()} open={false} onClose={onClose} />);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onClose).not.toHaveBeenCalled();

      rerender(<Rail {...baseProps()} open onClose={onClose} />);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(onClose).toHaveBeenCalledOnce();
    });

    it('calls onClose when the backdrop is clicked', () => {
      const onClose = vi.fn();
      const { container } = render(<Rail {...baseProps()} open onClose={onClose} />);
      fireEvent.click(container.querySelector('.chat-rail-backdrop')!);
      expect(onClose).toHaveBeenCalledOnce();
    });
  });
});
