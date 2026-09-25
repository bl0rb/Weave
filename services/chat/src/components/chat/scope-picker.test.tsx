// @vitest-environment jsdom
//
// Covers the composer's knowledge-space popover: the pill button's own
// label (delegated to `scopeLabel`, see lib/chat-types.test.ts for the
// pure label-logic/empty-selection-semantics cases), opening/closing it,
// and that "Alle meine Bereiche" really does mean "clear the selection"
// rather than some other empty-ish state. See vitest.config.ts's own
// docstring for why this file opts into jsdom via a per-file pragma while
// the rest of the suite stays plain Node.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ScopePicker } from '@/components/chat/scope-picker';
import type { Collection } from '@/types/weave-api';

const COLLECTIONS: Collection[] = [
  { slug: 'legal-2026', name: 'Legal 2026', description: null, public: false },
  { slug: 'hr-docs', name: 'HR Docs', description: null, public: true },
];

afterEach(() => cleanup());

describe('ScopePicker button label', () => {
  it('reads "Alle Bereiche" for an empty selection', () => {
    render(
      <ScopePicker collections={COLLECTIONS} selectedCollections={[]} onToggleCollection={() => {}} onClearCollections={() => {}} />
    );
    expect(screen.getByRole('button', { name: /Alle Bereiche/ })).toBeTruthy();
  });

  it('reads the collection\'s own name for a single selection', () => {
    render(
      <ScopePicker
        collections={COLLECTIONS}
        selectedCollections={['legal-2026']}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
      />
    );
    expect(screen.getByRole('button', { name: /Legal 2026/ })).toBeTruthy();
  });

  it('reads "N Bereiche" for several selections', () => {
    render(
      <ScopePicker
        collections={COLLECTIONS}
        selectedCollections={['legal-2026', 'hr-docs']}
        onToggleCollection={() => {}}
        onClearCollections={() => {}}
      />
    );
    expect(screen.getByRole('button', { name: /2 Bereiche/ })).toBeTruthy();
  });
});

describe('ScopePicker popover', () => {
  it('opens on click, lists one checkbox per collection plus "Alle meine Bereiche", and calls back on toggle', () => {
    const onToggleCollection = vi.fn();
    render(
      <ScopePicker
        collections={COLLECTIONS}
        selectedCollections={[]}
        onToggleCollection={onToggleCollection}
        onClearCollections={() => {}}
      />
    );

    const button = screen.getByRole('button', { name: /Alle Bereiche/ });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');

    expect(screen.getByRole('checkbox', { name: 'Alle meine Bereiche' })).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: /Legal 2026/ }));
    expect(onToggleCollection).toHaveBeenCalledWith('legal-2026');
  });

  it('treats "Alle meine Bereiche" as clearing the selection, not some other empty-ish state', () => {
    const onClearCollections = vi.fn();
    render(
      <ScopePicker
        collections={COLLECTIONS}
        selectedCollections={['legal-2026']}
        onToggleCollection={() => {}}
        onClearCollections={onClearCollections}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: /Legal 2026/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alle meine Bereiche' }));
    expect(onClearCollections).toHaveBeenCalledOnce();
  });

  it('closes on Escape and returns focus to the pill button', () => {
    render(
      <ScopePicker collections={COLLECTIONS} selectedCollections={[]} onToggleCollection={() => {}} onClearCollections={() => {}} />
    );
    const button = screen.getByRole('button', { name: /Alle Bereiche/ });
    fireEvent.click(button);
    expect(screen.getByRole('group')).toBeTruthy();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('group')).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it('closes on an outside click', () => {
    render(
      <div>
        <button type="button">Außerhalb</button>
        <ScopePicker collections={COLLECTIONS} selectedCollections={[]} onToggleCollection={() => {}} onClearCollections={() => {}} />
      </div>
    );
    fireEvent.click(screen.getByRole('button', { name: /Alle Bereiche/ }));
    expect(screen.getByRole('group')).toBeTruthy();

    fireEvent.mouseDown(screen.getByRole('button', { name: 'Außerhalb' }));
    expect(screen.queryByRole('group')).toBeNull();
  });
});
