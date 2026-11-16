import { describe, expect, it } from 'vitest';
import { buildBrandingCss, parseBranding, validPalette } from '@/lib/branding';

const PALETTE = {
  light: { accent: '#3355aa', accent_hover: '#2b4891', accent_ink: '#1f3366', accent_soft: '#e8ecf5', on_accent: '#ffffff' },
  dark: { accent: '#8fa6dd', accent_hover: '#9fb3e2', accent_ink: '#c9d4ef', accent_soft: '#2c3448', on_accent: '#0c1511' },
};
const SHA = 'ab'.repeat(32);

describe('buildBrandingCss', () => {
  it('sets all five tokens for light, manual dark and system dark with html:root selectors', () => {
    const css = buildBrandingCss(PALETTE)!;
    expect(css).toBe(
      'html:root{--accent:#3355aa;--accent-hover:#2b4891;--accent-ink:#1f3366;--accent-soft:#e8ecf5;--on-accent:#ffffff}' +
        'html:root.dark{--accent:#8fa6dd;--accent-hover:#9fb3e2;--accent-ink:#c9d4ef;--accent-soft:#2c3448;--on-accent:#0c1511}' +
        '@media (prefers-color-scheme: dark){html:root:not(.light){--accent:#8fa6dd;--accent-hover:#9fb3e2;--accent-ink:#c9d4ef;--accent-soft:#2c3448;--on-accent:#0c1511}}',
    );
  });

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a string', 'html{}'],
    ['a missing dark set', { light: PALETTE.light }],
    ['a missing key', { ...PALETTE, dark: { ...PALETTE.dark, on_accent: undefined } }],
    ['upper-case hex', { ...PALETTE, light: { ...PALETTE.light, accent: '#3355AA' } }],
    ['short hex', { ...PALETTE, light: { ...PALETTE.light, accent: '#35a' } }],
    ['a named colour', { ...PALETTE, light: { ...PALETTE.light, accent: 'red' } }],
    ['CSS injection', { ...PALETTE, dark: { ...PALETTE.dark, accent: '#000000}body{display:none' } }],
    ['a non-string value', { ...PALETTE, light: { ...PALETTE.light, accent: 0x3355aa } }],
  ])('injects nothing for %s', (_label, palette) => {
    expect(buildBrandingCss(palette)).toBeNull();
    expect(validPalette(palette)).toBeNull();
  });
});

describe('parseBranding', () => {
  it('keeps a valid palette and rewrites asset URLs to the chat-local proxy', () => {
    const branding = parseBranding({
      primary_color: '#3355aa',
      accent_color: null,
      palette: PALETTE,
      logo: { content_type: 'image/svg+xml', sha256: SHA, size_bytes: 120, url: '/v1/branding/assets/logo?v=x' },
      busy: { content_type: 'image/gif', sha256: SHA, size_bytes: 4000, url: 'https://evil.example/x.gif' },
      updated_at: '2026-11-16T10:00:00Z',
    });
    expect(branding).toEqual({
      palette: PALETTE,
      logo: { contentType: 'image/svg+xml', url: `/api/branding/assets/logo?v=${SHA.slice(0, 16)}` },
      busy: { contentType: 'image/gif', url: `/api/branding/assets/busy?v=${SHA.slice(0, 16)}` },
    });
  });

  it('returns the all-null default unchanged', () => {
    expect(parseBranding({ primary_color: null, accent_color: null, palette: null, logo: null, busy: null, updated_at: null })).toEqual({
      palette: null,
      logo: null,
      busy: null,
    });
  });

  it('drops an asset with a disallowed type or a malformed sha256, and an invalid palette', () => {
    expect(
      parseBranding({
        palette: { light: PALETTE.light },
        logo: { content_type: 'text/html', sha256: SHA },
        busy: { content_type: 'image/png', sha256: 'zz'.repeat(32) },
      }),
    ).toEqual({ palette: null, logo: null, busy: null });
    expect(parseBranding({ logo: { content_type: 'image/png', sha256: `${SHA}/../x` } })?.logo).toBeNull();
  });

  it.each([null, 'x', 3, []])('returns null for a non-object body (%j)', (body) => {
    expect(parseBranding(body)).toBeNull();
  });
});
