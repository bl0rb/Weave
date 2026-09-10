// @vitest-environment jsdom
//
// Covers FINDING 4: the Bot and Collections lists' loading/error/empty/
// populated states must live inside an `aria-live` region so a screen
// reader announces the transition instead of it happening silently. See
// vitest.config.ts's own docstring for why this file opts into jsdom via a
// per-file pragma while the rest of the suite stays plain Node.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Sidebar } from '@/components/chat/sidebar';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: () => {}, refresh: () => {} }),
}));

describe('Sidebar accessibility', () => {
  beforeEach(() => {
    // jsdom does not implement matchMedia; ThemeToggle reads it on mount.
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

  it('renders the Bot loading state inside a polite live region', () => {
    render(
      <Sidebar
        bots={null}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={null}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    const loading = screen.getByText('Bots werden geladen…');
    expect(loading.closest('[aria-live="polite"]')).not.toBeNull();
  });

  it('renders the Collections loading state inside a polite live region', () => {
    render(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={null}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    const loading = screen.getByText('Collections werden geladen…');
    expect(loading.closest('[aria-live="polite"]')).not.toBeNull();
  });

  it('keeps the empty-Bots message inside the same live region once loading finishes', () => {
    render(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={[]}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    const empty = screen.getByText('Für dich sind keine Bots verfügbar.');
    expect(empty.closest('[aria-live="polite"]')).not.toBeNull();
  });
});

const COLLECTIONS = [
  { slug: 'legal-2026', name: 'Legal 2026', description: null, public: false },
  { slug: 'hr-docs', name: 'HR Docs', description: null, public: true },
];

describe('Sidebar collection filter', () => {
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
  });

  afterEach(() => cleanup());

  it('marks a selected collection as pressed and calls onToggleCollection with its slug when clicked', () => {
    const onToggleCollection = vi.fn();
    render(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={COLLECTIONS}
        collectionsError={null}
        selectedCollections={['hr-docs']}
        onToggleCollection={onToggleCollection}
        onClearCollections={() => {}}
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );

    const legal = screen.getByRole('button', { name: /Legal 2026/ });
    const hr = screen.getByRole('button', { name: /HR Docs/ });
    expect(legal.getAttribute('aria-pressed')).toBe('false');
    expect(hr.getAttribute('aria-pressed')).toBe('true');

    legal.click();
    expect(onToggleCollection).toHaveBeenCalledWith('legal-2026');
  });

  it('only shows "Auswahl aufheben" once something is selected, and it clears the selection', () => {
    const onClearCollections = vi.fn();
    const { rerender } = render(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={COLLECTIONS}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={onClearCollections}
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    expect(screen.queryByText('Auswahl aufheben')).toBeNull();

    rerender(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={COLLECTIONS}
        collectionsError={null}
        selectedCollections={['legal-2026']}
        onToggleCollection={() => {}}
        onClearCollections={onClearCollections}
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    const clearButton = screen.getByText('Auswahl aufheben');
    clearButton.click();
    expect(onClearCollections).toHaveBeenCalledOnce();
  });
});

const CONVERSATIONS = [
  { id: 'conv-1', bot_id: 'faq-bot', title: 'VPN-Frage', created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-02T10:00:00Z' },
  { id: 'conv-2', bot_id: 'faq-bot', title: null, created_at: '2026-01-01T09:00:00Z', updated_at: '2026-01-01T09:00:00Z' },
];

describe('Sidebar history', () => {
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
  });

  afterEach(() => cleanup());

  it('shows a placeholder while the history list is loading, then the titled entries once loaded', () => {
    const { rerender } = render(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={[]}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={null}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    expect(screen.getByText('Verlauf wird geladen…')).not.toBeNull();

    rerender(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={[]}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    expect(screen.getByText('VPN-Frage')).not.toBeNull();
    expect(screen.getByText('Ohne Titel')).not.toBeNull();
  });

  it('calls onSelectConversation when an entry is clicked, and marks the active one as pressed', () => {
    const onSelectConversation = vi.fn();
    render(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={[]}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId="conv-2"
        onSelectConversation={onSelectConversation}
        onDeleteConversation={() => {}}
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
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={[]}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={CONVERSATIONS}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={onDeleteConversation}
      />
    );

    screen.getAllByRole('button', { name: 'Konversation löschen' })[0].click();
    expect(onDeleteConversation).toHaveBeenCalledWith('conv-1');
  });

  it('shows an empty-history message when there are no conversations yet', () => {
    render(
      <Sidebar
        bots={[]}
        botsError={null}
        selectedBotId={null}
        onSelectBot={() => {}}
        collections={[]}
        collectionsError={null}
        selectedCollections={[]}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
        conversations={[]}
        conversationsError={null}
        selectedConversationId={null}
        onSelectConversation={() => {}}
        onDeleteConversation={() => {}}
      />
    );
    expect(screen.getByText('Noch keine gespeicherten Konversationen.')).not.toBeNull();
  });
});
