// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BRANDING_FINITE_MS, WeaveIngestLogo } from './weave-ingest-logo';

const branding = vi.hoisted(() => ({ logoUrl: null as string | null, busyUrl: null as string | null, refresh: async () => {} }));
vi.mock('@/components/branding-provider', () => ({ useBranding: () => branding }));

const LOGO = 'https://api.example/api/v1/branding/assets/logo?v=0123456789abcdef';
const BUSY = 'https://api.example/api/v1/branding/assets/busy?v=0123456789abcdef';
let reducedMotion = false;

function view(animation?: 'loop' | 'finite') {
  const { container } = render(<WeaveIngestLogo className="h-8 w-8" animation={animation} />);
  return container;
}
const img = (container: HTMLElement) => container.querySelector('img');
const mark = (container: HTMLElement) => container.querySelector('svg.weave-logo');

beforeEach(() => {
  branding.logoUrl = null;
  branding.busyUrl = null;
  reducedMotion = false;
  window.matchMedia = ((query: string) => ({
    matches: reducedMotion && query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('without custom branding', () => {
  it('renders the built-in mark with its CSS animation', () => {
    const container = view('loop');
    expect(img(container)).toBeNull();
    expect(mark(container)?.getAttribute('class')).toContain('weave-logo--loop');
    expect(mark(container)?.getAttribute('class')).toContain('h-8 w-8');
  });
});

describe('with a custom logo only', () => {
  beforeEach(() => { branding.logoUrl = LOGO; });

  it('shows the logo as an <img>, at rest and while busy', () => {
    for (const animation of [undefined, 'loop', 'finite'] as const) {
      const container = view(animation);
      const image = img(container);
      expect(image?.getAttribute('src')).toBe(LOGO);
      expect(image?.getAttribute('alt')).toBe('');
      expect(image?.getAttribute('aria-hidden')).toBe('true');
      expect(image?.getAttribute('draggable')).toBe('false');
      expect(image?.className).toContain('object-contain');
      expect(image?.className).toContain('h-8 w-8');
      expect(mark(container)).toBeNull();
      cleanup();
    }
  });
});

describe('with a custom busy animation only', () => {
  beforeEach(() => { branding.busyUrl = BUSY; });

  it('shows the built-in mark without animation at rest', () => {
    const container = view();
    expect(img(container)).toBeNull();
    expect(mark(container)?.getAttribute('class')).not.toContain('weave-logo--');
  });

  it('shows the busy asset while looping', () => {
    expect(img(view('loop'))?.getAttribute('src')).toBe(BUSY);
  });

  it('falls back to the animated built-in mark under reduced motion', () => {
    reducedMotion = true;
    const container = view('loop');
    expect(img(container)).toBeNull();
    expect(mark(container)?.getAttribute('class')).toContain('weave-logo--loop');
  });
});

describe('with a custom logo and busy animation', () => {
  beforeEach(() => {
    branding.logoUrl = LOGO;
    branding.busyUrl = BUSY;
  });

  it('loops with the busy asset, and never shows it under reduced motion', () => {
    expect(img(view('loop'))?.getAttribute('src')).toBe(BUSY);
    cleanup();
    reducedMotion = true;
    expect(img(view('loop'))?.getAttribute('src')).toBe(LOGO);
    cleanup();
    expect(img(view('finite'))?.getAttribute('src')).toBe(LOGO);
  });

  it('finite shows the busy asset for four seconds, then the logo', () => {
    vi.useFakeTimers();
    const container = view('finite');
    expect(img(container)?.getAttribute('src')).toBe(BUSY);
    act(() => { vi.advanceTimersByTime(BRANDING_FINITE_MS - 1); });
    expect(img(container)?.getAttribute('src')).toBe(BUSY);
    act(() => { vi.advanceTimersByTime(1); });
    expect(img(container)?.getAttribute('src')).toBe(LOGO);
  });

  it('shows the logo without animation', () => {
    expect(img(view())?.getAttribute('src')).toBe(LOGO);
  });

  it('falls back when an asset fails to load: busy to the logo, the logo to the built-in mark', () => {
    const container = view('loop');
    fireEvent.error(img(container)!);
    expect(img(container)?.getAttribute('src')).toBe(LOGO);
    fireEvent.error(img(container)!);
    expect(img(container)).toBeNull();
    expect(mark(container)?.getAttribute('class')).toContain('weave-logo--loop');
  });
});
