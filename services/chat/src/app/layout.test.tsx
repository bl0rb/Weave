import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BrandingProvider } from '@/components/branding-provider';
import RootLayout, { generateMetadata } from './layout';

vi.mock('next/font/google', () => ({ Source_Sans_3: () => ({ variable: 'font-sans' }) }));
vi.mock('@/i18n/server', () => ({
  getLocale: async () => 'de',
  getTranslator: async () => ({ locale: 'de', t: (key: string) => key }),
}));

const SHA = 'ef'.repeat(32);
const PALETTE = {
  light: { accent: '#3355aa', accent_hover: '#2b4891', accent_ink: '#1f3366', accent_soft: '#e8ecf5', on_accent: '#ffffff' },
  dark: { accent: '#8fa6dd', accent_hover: '#9fb3e2', accent_ink: '#c9d4ef', accent_soft: '#2c3448', on_accent: '#0c1511' },
};

function upstreamBranding(body: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })));
}

type AnyElement = ReactElement<Record<string, unknown> & { children?: ReactNode }>;

/** Depth-first search through the element tree the layout returns (no rendering). */
function find(node: ReactNode, match: (element: AnyElement) => boolean): AnyElement | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = find(child, match);
      if (found) return found;
    }
    return null;
  }
  if (!isValidElement(node)) return null;
  const element = node as AnyElement;
  if (match(element)) return element;
  return find(element.props.children, match);
}

async function layoutTree() {
  return RootLayout({ children: <main /> });
}

describe('root layout branding', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('without branding: no style, no icons override (app/icon.svg stays), no custom assets', async () => {
    upstreamBranding({ primary_color: null, accent_color: null, palette: null, logo: null, busy: null, updated_at: null });

    const metadata = await generateMetadata();
    expect(metadata.icons).toBeUndefined();
    expect(metadata.title).toBe('common.appTitle');

    const tree = await layoutTree();
    expect(find(tree, (el) => el.type === 'style')).toBeNull();
    expect(find(tree, (el) => el.type === BrandingProvider)?.props).toMatchObject({ logoUrl: null, busyUrl: null });
  });

  it('with branding: injects the palette style, uses the logo as tab icon and provides both asset URLs', async () => {
    upstreamBranding({
      palette: PALETTE,
      logo: { content_type: 'image/svg+xml', sha256: SHA, size_bytes: 10, url: '/v1/branding/assets/logo?v=x' },
      busy: { content_type: 'image/gif', sha256: SHA, size_bytes: 10, url: '/v1/branding/assets/busy?v=x' },
    });
    const logoUrl = `/api/branding/assets/logo?v=${SHA.slice(0, 16)}`;
    const busyUrl = `/api/branding/assets/busy?v=${SHA.slice(0, 16)}`;

    const metadata = await generateMetadata();
    expect(metadata.icons).toEqual({ icon: [{ url: logoUrl, type: 'image/svg+xml' }] });

    const tree = await layoutTree();
    const style = find(tree, (el) => el.type === 'style');
    expect(style?.props.id).toBe('weave-branding');
    const css = (style?.props.dangerouslySetInnerHTML as { __html: string }).__html;
    expect(css).toContain('html:root{--accent:#3355aa;');
    expect(css).toContain('html:root.dark{--accent:#8fa6dd;');
    expect(css).toContain('@media (prefers-color-scheme: dark){html:root:not(.light){--accent:#8fa6dd;');
    expect(find(tree, (el) => el.type === BrandingProvider)?.props).toMatchObject({ logoUrl, busyUrl });
  });

  it('renders today\'s look when Weave-API fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));

    expect((await generateMetadata()).icons).toBeUndefined();
    const tree = await layoutTree();
    expect(find(tree, (el) => el.type === 'style')).toBeNull();
    expect(find(tree, (el) => el.type === BrandingProvider)?.props).toMatchObject({ logoUrl: null, busyUrl: null });
  });
});
