// @vitest-environment jsdom
//
// The conversation history moved out of the left sidebar into its own
// collapsible right-hand column — these are the sidebar's former history
// tests, plus the collapse toggle that only exists here.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { HistoryPanel } from '@/components/chat/history-panel';

const CONVERSATIONS = [
  { id: 'conv-1', bot_id: 'faq-bot', title: 'VPN-Frage', created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-02T10:00:00Z' },
  { id: 'conv-2', bot_id: 'faq-bot', title: null, created_at: '2026-01-01T09:00:00Z', updated_at: '2026-01-01T09:00:00Z' },
];

describe('HistoryPanel', () => {
  afterEach(() => cleanup());

  it('shows a placeholder while the history list is loading, then the titled entries once loaded', () => {
    const { rerender } = render(
      <HistoryPanel
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
        onDeleteAllConversations={() => {}}
      />
    );
    expect(screen.getByText('Verlauf wird geladen…')).not.toBeNull();

    rerender(
      <HistoryPanel
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
        onDeleteAllConversations={() => {}}
      />
    );
    expect(screen.getByText('VPN-Frage')).not.toBeNull();
    expect(screen.getByText('Ohne Titel')).not.toBeNull();
  });

  it('calls onSelectConversation when an entry is clicked, and marks the active one as pressed', () => {
    const onSelectConversation = vi.fn();
    render(
      <HistoryPanel
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId="conv-2"
        onSelectConversation={onSelectConversation}
        onDeleteConversation={() => {}}
        onDeleteAllConversations={() => {}}
      />
    );

    const active = screen.getByRole('button', { name: /Ohne Titel/ });
    expect(active.getAttribute('aria-pressed')).toBe('true');

    screen.getByRole('button', { name: /VPN-Frage/ }).click();
    expect(onSelectConversation).toHaveBeenCalledWith('conv-1');
  });

  it('calls onDeleteConversation with the entry id when its delete button is clicked', () => {
    const onDeleteConversation = vi.fn();
    render(
      <HistoryPanel
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={onDeleteConversation}
        onDeleteAllConversations={() => {}}
      />
    );

    screen.getAllByRole('button', { name: 'Konversation löschen' })[0].click();
    expect(onDeleteConversation).toHaveBeenCalledWith('conv-1');
  });

  it('offers deleting the whole history only while entries exist', () => {
    const onDeleteAllConversations = vi.fn();
    const { rerender } = render(
      <HistoryPanel
        conversations={[]}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
        onDeleteAllConversations={onDeleteAllConversations}
      />
    );
    expect(screen.getByText('Noch keine gespeicherten Konversationen.')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Verlauf löschen' })).toBeNull();

    rerender(
      <HistoryPanel
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
        onDeleteAllConversations={onDeleteAllConversations}
      />
    );
    screen.getByRole('button', { name: 'Verlauf löschen' }).click();
    expect(onDeleteAllConversations).toHaveBeenCalledOnce();
  });

  it('collapses the panel and restores it from the collapsed strip', async () => {
    render(
      <HistoryPanel
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
        onDeleteAllConversations={() => {}}
      />
    );

    screen.getByRole('button', { name: 'Verlauf ausblenden' }).click();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Verlauf einblenden' })).not.toBeNull();
      expect(screen.queryByRole('button', { name: 'Verlauf ausblenden' })).toBeNull();
    });

    screen.getByRole('button', { name: 'Verlauf einblenden' }).click();
    await waitFor(() => {
      expect(screen.getByText('VPN-Frage')).not.toBeNull();
    });
  });
});
