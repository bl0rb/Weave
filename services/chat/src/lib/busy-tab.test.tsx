// @vitest-environment jsdom
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BUSY_FAVICON_FRAMES, BUSY_TITLE_PREFIX, useBusyTabIndicator } from './busy-tab';

function Probe({ busy }: { busy: boolean }) {
  useBusyTabIndicator(busy);
  return null;
}

describe('useBusyTabIndicator', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.head.innerHTML = '<link rel="icon" href="/icon.svg">';
    document.title = 'Weave';
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('prefixes the title and cycles the favicon while busy, then restores both', () => {
    const view = render(<Probe busy />);
    const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']")!;
    expect(document.title).toBe(`${BUSY_TITLE_PREFIX}Weave`);
    act(() => { vi.advanceTimersByTime(700); });
    expect(link.getAttribute('href')).toBe(BUSY_FAVICON_FRAMES[1]);
    act(() => { vi.advanceTimersByTime(700); });
    expect(link.getAttribute('href')).toBe(BUSY_FAVICON_FRAMES[2]);

    view.rerender(<Probe busy={false} />);
    expect(document.title).toBe('Weave');
    expect(link.getAttribute('href')).toBe('/icon.svg');
  });

  it('does nothing while idle', () => {
    render(<Probe busy={false} />);
    act(() => { vi.advanceTimersByTime(2000); });
    expect(document.title).toBe('Weave');
    expect(document.querySelector("link[rel~='icon']")!.getAttribute('href')).toBe('/icon.svg');
  });
});
