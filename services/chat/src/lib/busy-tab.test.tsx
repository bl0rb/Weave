// @vitest-environment jsdom
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BrandingProvider } from '@/components/branding-provider';
import { BUSY_FAVICON_FRAMES, BUSY_TITLE_PREFIX, useBusyTabIndicator } from './busy-tab';

function Probe({ busy }: { busy: boolean }) {
  useBusyTabIndicator(busy);
  return null;
}

const LOGO = '/api/branding/assets/logo?v=0123456789abcdef';
const BUSY = '/api/branding/assets/busy?v=fedcba9876543210';

function Branded({ busy, logoUrl = null, busyUrl = null }: { busy: boolean; logoUrl?: string | null; busyUrl?: string | null }) {
  return (
    <BrandingProvider logoUrl={logoUrl} busyUrl={busyUrl}>
      <Probe busy={busy} />
    </BrandingProvider>
  );
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

  it('with a custom busy asset shows it as the icon while busy, then restores the logo icon and its type', () => {
    document.head.innerHTML = `<link rel="icon" href="${LOGO}" type="image/png">`;
    document.title = 'Weave';
    const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']")!;
    const view = render(<Branded busy logoUrl={LOGO} busyUrl={BUSY} />);
    expect(document.title).toBe(`${BUSY_TITLE_PREFIX}Weave`);
    expect(link.getAttribute('href')).toBe(BUSY);
    expect(link.hasAttribute('type')).toBe(false);
    act(() => { vi.advanceTimersByTime(2100); });
    expect(link.getAttribute('href')).toBe(BUSY);

    view.rerender(<Branded busy={false} logoUrl={LOGO} busyUrl={BUSY} />);
    expect(document.title).toBe('Weave');
    expect(link.getAttribute('href')).toBe(LOGO);
    expect(link.getAttribute('type')).toBe('image/png');
  });

  it('with only a custom busy asset restores the built-in icon afterwards', () => {
    const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']")!;
    const view = render(<Branded busy busyUrl={BUSY} />);
    expect(link.getAttribute('href')).toBe(BUSY);
    view.rerender(<Branded busy={false} busyUrl={BUSY} />);
    expect(link.getAttribute('href')).toBe('/icon.svg');
  });

  it('with only a custom logo keeps the icon and only prefixes the title', () => {
    document.head.innerHTML = `<link rel="icon" href="${LOGO}" type="image/svg+xml">`;
    document.title = 'Weave';
    const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']")!;
    const view = render(<Branded busy logoUrl={LOGO} />);
    expect(document.title).toBe(`${BUSY_TITLE_PREFIX}Weave`);
    act(() => { vi.advanceTimersByTime(2100); });
    expect(link.getAttribute('href')).toBe(LOGO);

    view.rerender(<Branded busy={false} logoUrl={LOGO} />);
    expect(document.title).toBe('Weave');
    expect(link.getAttribute('href')).toBe(LOGO);
    expect(link.getAttribute('type')).toBe('image/svg+xml');
  });
});
