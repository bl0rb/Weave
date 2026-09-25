// @vitest-environment jsdom
//
// Covers the two distinct GuardTrace.reason values that both mean "no
// source-backed answer was possible" but for different, distinguishable
// causes (contracts/internal-chat.md): "no_collections" (caller/bot share
// no readable collection at all — nothing the caller did this turn can fix
// it) vs. "filter_excluded_all" (a non-empty readable scope existed, but
// THIS turn's own sidebar collection filter narrowed it to nothing — fixable
// by the caller). See vitest.config.ts's own docstring for why this file
// opts into jsdom via a per-file pragma while the rest of the suite stays
// plain Node.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { GuardBanner } from '@/components/chat/guard-banner';

describe('GuardBanner', () => {
  afterEach(() => cleanup());

  it('renders nothing when the guard did not trigger', () => {
    const { container } = render(<GuardBanner guard={{ triggered: false, reason: null }} />);
    expect(container.innerHTML).toBe('');
  });

  it('explains "no_collections" as an access-rights situation, with no fix named', () => {
    render(<GuardBanner guard={{ triggered: true, reason: 'no_collections' }} />);
    expect(screen.getByText(/keine Collection zur Verfügung, die dieser Bot durchsuchen darf/)).toBeTruthy();
    expect(screen.queryByText(/Auswahl aufheben/)).toBeNull();
  });

  it('explains "filter_excluded_all" as the caller\'s own filter, and names the fix ("Auswahl aufheben")', () => {
    const { container } = render(<GuardBanner guard={{ triggered: true, reason: 'filter_excluded_all' }} />);
    expect(screen.getByText(/Deine Auswahl der Wissensbereiche schließt alle Collections aus/)).toBeTruthy();
    expect(container.textContent).toMatch(/Auswahl aufheben/);
  });

  it('renders genuinely different text for the two reasons', () => {
    const { unmount } = render(<GuardBanner guard={{ triggered: true, reason: 'no_collections' }} />);
    const noCollectionsText = screen.getByText(/keine Collection zur Verfügung/).textContent;
    unmount();

    render(<GuardBanner guard={{ triggered: true, reason: 'filter_excluded_all' }} />);
    const filterExcludedText = screen.getByText(/Deine Auswahl der Wissensbereiche/).textContent;

    expect(noCollectionsText).not.toBe(filterExcludedText);
  });

  it('only offers the reset-and-retry action for "filter_excluded_all", and only when a handler is given', () => {
    const onResetScopeAndRetry = vi.fn();

    const { rerender } = render(
      <GuardBanner guard={{ triggered: true, reason: 'no_collections' }} onResetScopeAndRetry={onResetScopeAndRetry} />
    );
    expect(screen.queryByRole('button', { name: /Auswahl zurücksetzen/ })).toBeNull();

    rerender(<GuardBanner guard={{ triggered: true, reason: 'filter_excluded_all' }} />);
    expect(screen.queryByRole('button', { name: /Auswahl zurücksetzen/ })).toBeNull();

    rerender(
      <GuardBanner guard={{ triggered: true, reason: 'filter_excluded_all' }} onResetScopeAndRetry={onResetScopeAndRetry} />
    );
    screen.getByRole('button', { name: /Auswahl zurücksetzen & neu fragen/ }).click();
    expect(onResetScopeAndRetry).toHaveBeenCalledOnce();
  });
});
