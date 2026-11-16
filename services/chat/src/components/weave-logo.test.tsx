// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { hydrateRoot, type Root } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
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

function brandedTree(branding: { logoUrl?: string | null; busyUrl?: string | null }, animation?: 'loop' | 'finite') {
  return (
    <BrandingProvider logoUrl={branding.logoUrl ?? null} busyUrl={branding.busyUrl ?? null}>
      <WeaveLogo className="h-8 w-8" animation={animation} />
    </BrandingProvider>
  );
}

// Server HTML in the document, as the browser has it before React hydrates.
const hydrated: { root: Root; container: HTMLElement }[] = [];
function serverRender(tree: ReactElement): HTMLElement {
  const container = document.createElement('div');
  container.innerHTML = renderToString(tree);
  document.body.appendChild(container);
  return container;
}

async function hydrate(container: HTMLElement, tree: ReactElement) {
  const recoverable = vi.fn();
  await act(async () => {
    hydrated.push({ root: hydrateRoot(container, tree, { onRecoverableError: recoverable }), container });
  });
  return recoverable;
}

/** The state a browser leaves an `<img>` in once its request failed. */
function failBeforeHydration(img: HTMLImageElement) {
  Object.defineProperties(img, { complete: { value: true }, naturalWidth: { value: 0 } });
  img.dispatchEvent(new Event('error'));
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
    for (const { root, container } of hydrated.splice(0)) {
      act(() => root.unmount());
      container.remove();
    }
    vi.useRealTimers();
    vi.restoreAllMocks();
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

  it('loop under prefers-reduced-motion: the browser picks the logo instead of the busy asset', () => {
    mockReducedMotion(true);
    const { container } = renderLogo({ logoUrl: LOGO, busyUrl: BUSY }, 'loop');
    const source = container.querySelector('picture > source')!;
    expect(source.getAttribute('media')).toBe('(prefers-reduced-motion: reduce)');
    expect(source.getAttribute('srcset')).toBe(LOGO);
    expect(container.querySelector('picture > img')!.getAttribute('src')).toBe(BUSY);
  });

  it('loop with only a busy asset: CSS shows the built-in mark instead under prefers-reduced-motion', () => {
    mockReducedMotion(true);
    const { container } = renderLogo({ busyUrl: BUSY }, 'loop');
    const img = container.querySelector('img')!;
    const svg = container.querySelector('svg')!;
    expect(img.getAttribute('src')).toBe(BUSY);
    expect(img.className).toBe('object-contain motion-reduce:hidden h-8 w-8');
    expect(svg.getAttribute('class')).toBe('weave-logo weave-logo--loop weave-logo--motion-weave motion-safe:hidden h-8 w-8');
  });

  it('server HTML already hides the busy asset from prefers-reduced-motion and hydrates without a mismatch', async () => {
    const picture = serverRender(brandedTree({ logoUrl: LOGO, busyUrl: BUSY }, 'loop'));
    const busyOnly = serverRender(brandedTree({ busyUrl: BUSY }, 'loop'));
    expect(picture.querySelector('picture > source')!.getAttribute('srcset')).toBe(LOGO);
    expect(picture.querySelector('picture > source')!.getAttribute('media')).toBe('(prefers-reduced-motion: reduce)');
    expect(picture.querySelector('picture > img')!.getAttribute('src')).toBe(BUSY);
    expect(busyOnly.querySelector('img')!.className).toContain('motion-reduce:hidden');
    expect(busyOnly.querySelector('svg')!.getAttribute('class')).toContain('motion-safe:hidden');

    mockReducedMotion(true);
    const consoleError = vi.spyOn(console, 'error');
    const pictureHtml = picture.innerHTML;
    expect(await hydrate(picture, brandedTree({ logoUrl: LOGO, busyUrl: BUSY }, 'loop'))).not.toHaveBeenCalled();
    expect(await hydrate(busyOnly, brandedTree({ busyUrl: BUSY }, 'loop'))).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    expect(picture.innerHTML).toBe(pictureHtml);
    expect(busyOnly.querySelector('img')!.getAttribute('src')).toBe(BUSY);
    expect(busyOnly.querySelector('svg')).not.toBeNull();
  });

  it('a broken logo under prefers-reduced-motion falls back to the built-in mark, not the busy asset', () => {
    mockReducedMotion(true);
    const { container } = renderLogo({ logoUrl: LOGO, busyUrl: BUSY }, 'loop');
    fireEvent.error(container.querySelector('img')!);
    expect(container.querySelector('picture')).toBeNull();
    expect(container.querySelector('img')!.className).toContain('motion-reduce:hidden');
    expect(container.querySelector('svg')!.getAttribute('class')).toContain('motion-safe:hidden');
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

  it('falls back for an asset that failed before hydration (React does not replay that error)', async () => {
    const tree = brandedTree({ logoUrl: LOGO });
    const container = serverRender(tree);
    failBeforeHydration(container.querySelector('img')!);

    await hydrate(container, tree);

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('svg.weave-logo')).not.toBeNull();
  });

  it('keeps an asset that loaded but reports no natural width (an SVG without width/height)', async () => {
    const tree = brandedTree({ logoUrl: LOGO });
    const container = serverRender(tree);
    const img = container.querySelector('img')!;
    Object.defineProperties(img, { complete: { value: true }, naturalWidth: { value: 0 }, decode: { value: () => Promise.resolve() } });

    await hydrate(container, tree);

    expect(container.querySelector('img')!.getAttribute('src')).toBe(LOGO);
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
