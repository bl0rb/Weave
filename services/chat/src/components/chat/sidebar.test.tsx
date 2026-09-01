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
      />
    );
    const clearButton = screen.getByText('Auswahl aufheben');
    clearButton.click();
    expect(onClearCollections).toHaveBeenCalledOnce();
  });
});
