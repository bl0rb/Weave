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
      />
    );
    const empty = screen.getByText('Für dich sind keine Bots verfügbar.');
    expect(empty.closest('[aria-live="polite"]')).not.toBeNull();
  });
});
