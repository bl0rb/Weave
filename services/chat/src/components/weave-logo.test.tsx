// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BrandingProvider } from '@/components/branding-provider';
import { WeaveLogo } from './weave-logo';

const LOGO = '/api/branding/assets/logo?v=0123456789abcdef';
const BUSY = '/api/branding/assets/busy?v=fedcba9876543210';

function mockReducedMotion(reduce: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: reduce && query === '(prefers-reduced-motion: reduce)',
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function renderLogo(
  branding: { logoUrl?: string | null; busyUrl?: string | null } | null,
  animation?: 'loop' | 'finite',
) {
  const logo = <WeaveLogo className="h-8 w-8" animation={animation} />;
  return render(branding ? <BrandingProvider logoUrl={branding.logoUrl ?? null} busyUrl={branding.busyUrl ?? null}>{logo}</BrandingProvider> : logo);
}

function shown(container: HTMLElement) {
  const img = container.querySelector('img');
  const svg = container.querySelector('svg');
  return { img: img?.getAttribute('src') ?? null, svgClass: svg?.getAttribute('class') ?? null };
}

describe('WeaveLogo', () => {
  beforeEach(() => {
    mockReducedMotion(false);
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('renders the built-in animated mark without branding, with or without a provider', () => {
    for (const branding of [null, { logoUrl: null, busyUrl: null }]) {
      const { container, unmount } = renderLogo(branding, 'loop');
      const { img, svgClass } = shown(container);
      expect(img).toBeNull();
      expect(svgClass).toContain('weave-logo--loop');
      expect(container.querySelectorAll('svg rect')).toHaveLength(12);
      unmount();
    }
  });

  it('renders the custom logo as a decorative <img> without animation', () => {
    const { container } = renderLogo({ logoUrl: LOGO, busyUrl: BUSY });
    const img = container.querySelector('img')!;
    expect(img.getAttribute('src')).toBe(LOGO);
    expect(img.getAttribute('alt')).toBe('');
    expect(img.getAttribute('aria-hidden')).toBe('true');
    expect(img.getAttribute('draggable')).toBe('false');
    expect(img.className).toBe('object-contain h-8 w-8');
    expect(container.querySelector('svg')).toBeNull();
  });

  it('renders the built-in mark at rest when only a busy asset is set and nothing moves', () => {
    const { container } = renderLogo({ busyUrl: BUSY });
    const { img, svgClass } = shown(container);
    expect(img).toBeNull();
    expect(svgClass).not.toContain('weave-logo--loop');
    expect(svgClass).not.toContain('weave-logo--finite');
  });

  it('loop: shows the busy asset, else the logo, else the animated built-in mark', () => {
    expect(shown(renderLogo({ logoUrl: LOGO, busyUrl: BUSY }, 'loop').container).img).toBe(BUSY);
    cleanup();
    expect(shown(renderLogo({ logoUrl: LOGO }, 'loop').container).img).toBe(LOGO);
    cleanup();
    expect(shown(renderLogo({ busyUrl: BUSY }, 'loop').container).img).toBe(BUSY);
  });

  it('loop under prefers-reduced-motion: never the busy asset', () => {
    mockReducedMotion(true);
    expect(shown(renderLogo({ logoUrl: LOGO, busyUrl: BUSY }, 'loop').container).img).toBe(LOGO);
    cleanup();
    const { img, svgClass } = shown(renderLogo({ busyUrl: BUSY }, 'loop').container);
    expect(img).toBeNull();
    expect(svgClass).toContain('weave-logo');
  });

  it('finite: shows the busy asset for four seconds, then the logo', () => {
    vi.useFakeTimers();
    const { container } = renderLogo({ logoUrl: LOGO, busyUrl: BUSY }, 'finite');
    expect(shown(container).img).toBe(BUSY);
    act(() => { vi.advanceTimersByTime(3999); });
    expect(shown(container).img).toBe(BUSY);
    act(() => { vi.advanceTimersByTime(1); });
    expect(shown(container).img).toBe(LOGO);
  });

  it('finite without a custom logo ends on the built-in mark at rest', () => {
    vi.useFakeTimers();
    const { container } = renderLogo({ busyUrl: BUSY }, 'finite');
    expect(shown(container).img).toBe(BUSY);
    act(() => { vi.advanceTimersByTime(4000); });
    const { img, svgClass } = shown(container);
    expect(img).toBeNull();
    expect(svgClass).not.toContain('weave-logo--finite');
  });

  it('falls back when an asset fails to load: busy -> logo -> built-in mark', () => {
    const { container } = renderLogo({ logoUrl: LOGO, busyUrl: BUSY }, 'loop');
    fireEvent.error(container.querySelector('img')!);
    expect(shown(container).img).toBe(LOGO);
    fireEvent.error(container.querySelector('img')!);
    const { img, svgClass } = shown(container);
    expect(img).toBeNull();
    expect(svgClass).toContain('weave-logo--loop');
  });

  it('loop -> finite starts the four seconds when the loop ends', () => {
    vi.useFakeTimers();
    const tree = (animation?: 'loop' | 'finite') => (
      <BrandingProvider logoUrl={LOGO} busyUrl={BUSY}>
        <WeaveLogo animation={animation} />
      </BrandingProvider>
    );
    const view = render(tree('loop'));
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(shown(view.container).img).toBe(BUSY);
    view.rerender(tree('finite'));
    act(() => { vi.advanceTimersByTime(3000); });
    expect(shown(view.container).img).toBe(BUSY);
    act(() => { vi.advanceTimersByTime(1000); });
    expect(shown(view.container).img).toBe(LOGO);
  });
});
