// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';

import {
  BRANDING_ICON_ID,
  BRANDING_INIT_SCRIPT,
  BRANDING_STORAGE_KEY,
  BRANDING_STYLE_ID,
  applyBrandingCss,
  applyBrandingFavicon,
  brandingToCache,
  buildBrandingCss,
  normalizeHexColor,
  parseBranding,
  readCachedBranding,
} from './branding';

const API = 'https://api.example';
const SHA = 'ab'.repeat(32);
const light = { accent: '#1d6a53', accent_hover: '#195a47', accent_ink: '#124536', accent_soft: '#e5f0ea', on_accent: '#ffffff' };
const dark = { accent: '#52b48b', accent_hover: '#67c39d', accent_ink: '#a6dcc3', accent_soft: '#233029', on_accent: '#0c1511' };
const palette = { light, dark };
const CSS =
  'html:root{--accent:#1d6a53;--accent-hover:#195a47;--accent-ink:#124536;--accent-soft:#e5f0ea;--on-accent:#ffffff}' +
  'html:root.dark{--accent:#52b48b;--accent-hover:#67c39d;--accent-ink:#a6dcc3;--accent-soft:#233029;--on-accent:#0c1511}';

afterEach(() => {
  document.head.innerHTML = '';
  window.localStorage.clear();
});

describe('normalizeHexColor', () => {
  it('accepts #rgb and #rrggbb in any case and returns lowercase #rrggbb', () => {
    expect(normalizeHexColor('#ABC')).toBe('#aabbcc');
    expect(normalizeHexColor(' #1D6A53 ')).toBe('#1d6a53');
  });

  it('rejects anything else', () => {
    for (const value of ['', '1d6a53', '#1d6a5', '#1d6a533', 'red', '#ggg', 'rgb(0,0,0)']) {
      expect(normalizeHexColor(value)).toBeNull();
    }
  });
});

describe('buildBrandingCss', () => {
  it('sets the five accent tokens for light and the manual dark class with html:root selectors', () => {
    expect(buildBrandingCss(palette)).toBe(CSS);
  });

  it('injects nothing for a missing, partial or non-hex palette', () => {
    expect(buildBrandingCss(null)).toBeNull();
    expect(buildBrandingCss({ light })).toBeNull();
    expect(buildBrandingCss({ light: { ...light, on_accent: undefined }, dark })).toBeNull();
    expect(buildBrandingCss({ light: { ...light, accent: '#1D6A53' }, dark })).toBeNull();
    expect(buildBrandingCss({ light, dark: { ...dark, accent: 'red' } })).toBeNull();
    expect(buildBrandingCss({ light: { ...light, accent: '#1d6a53}body{display:none' }, dark })).toBeNull();
  });
});

describe('parseBranding', () => {
  it('builds asset URLs on the API base from kind and hash, never from the response url', () => {
    const branding = parseBranding({
      palette,
      logo: { content_type: 'image/svg+xml', sha256: SHA, size_bytes: 10, url: 'https://evil.example/x.svg' },
      busy: { content_type: 'image/gif', sha256: SHA, size_bytes: 10, url: '/api/v1/branding/assets/busy?v=x' },
    }, API);
    expect(branding?.palette).toEqual(palette);
    expect(branding?.logo).toEqual({ contentType: 'image/svg+xml', url: `${API}/api/v1/branding/assets/logo?v=${SHA.slice(0, 16)}` });
    expect(branding?.busy?.url).toBe(`${API}/api/v1/branding/assets/busy?v=${SHA.slice(0, 16)}`);
  });

  it('drops assets with an unknown type or a malformed hash, and non-objects entirely', () => {
    const branding = parseBranding({
      palette: null,
      logo: { content_type: 'text/html', sha256: SHA },
      busy: { content_type: 'image/png', sha256: 'abc' },
    }, API);
    expect(branding).toEqual({ palette: null, logo: null, busy: null });
    expect(parseBranding('nope', API)).toBeNull();
    expect(parseBranding(null, API)).toBeNull();
  });
});

describe('cache', () => {
  const branding = parseBranding({ palette, logo: { content_type: 'image/png', sha256: SHA }, busy: null }, API);

  it('round-trips what the provider applied', () => {
    const cached = brandingToCache(branding);
    expect(cached).toEqual({ css: CSS, logoUrl: `${API}/api/v1/branding/assets/logo?v=${SHA.slice(0, 16)}`, busyUrl: null });
    expect(readCachedBranding(JSON.stringify(cached), API)).toEqual(cached);
  });

  it('validates again: tampered css and foreign URLs are dropped', () => {
    const tampered = JSON.stringify({
      css: `${CSS}body{display:none}`,
      logoUrl: 'https://evil.example/api/v1/branding/assets/logo?v=abababababababab',
      busyUrl: `${API}/api/v1/branding/assets/busy?v=abababababababab"`,
    });
    expect(readCachedBranding(tampered, API)).toBeNull();
    expect(readCachedBranding('{not json', API)).toBeNull();
    // A cache from another API base is not used either.
    expect(readCachedBranding(JSON.stringify(brandingToCache(branding)), 'https://other.example')).toEqual({ css: CSS, logoUrl: null, busyUrl: null });
  });
});

describe('BRANDING_INIT_SCRIPT', () => {
  function runInitScript() {
    new Function(BRANDING_INIT_SCRIPT)();
  }

  it('applies the cached colours before paint', () => {
    window.localStorage.setItem(BRANDING_STORAGE_KEY, JSON.stringify({ css: CSS, logoUrl: null, busyUrl: null }));
    runInitScript();
    expect(document.getElementById(BRANDING_STYLE_ID)?.textContent).toBe(CSS);
  });

  it('injects nothing that is not built from validated hex values', () => {
    for (const css of [`${CSS}body{display:none}`, 'html:root{--accent:red}', '</style><script>alert(1)</script>', 42]) {
      window.localStorage.setItem(BRANDING_STORAGE_KEY, JSON.stringify({ css }));
      runInitScript();
      expect(document.getElementById(BRANDING_STYLE_ID)).toBeNull();
    }
    window.localStorage.setItem(BRANDING_STORAGE_KEY, '{broken');
    runInitScript();
    expect(document.getElementById(BRANDING_STYLE_ID)).toBeNull();
  });
});

describe('applyBrandingCss', () => {
  it('injects, replaces and removes the style element', () => {
    applyBrandingCss(CSS);
    expect(document.querySelectorAll(`#${BRANDING_STYLE_ID}`)).toHaveLength(1);
    const other = CSS.replace('#1d6a53', '#aa0000');
    applyBrandingCss(other);
    expect(document.querySelectorAll(`#${BRANDING_STYLE_ID}`)).toHaveLength(1);
    expect(document.getElementById(BRANDING_STYLE_ID)?.textContent).toBe(other);
    applyBrandingCss(null);
    expect(document.getElementById(BRANDING_STYLE_ID)).toBeNull();
  });

  it('refuses text that is not branding css', () => {
    applyBrandingCss(CSS);
    applyBrandingCss('body{display:none}');
    expect(document.getElementById(BRANDING_STYLE_ID)).toBeNull();
  });
});

describe('applyBrandingFavicon', () => {
  it('parks the built-in icons while the custom logo is the tab icon, and restores them', () => {
    document.head.innerHTML =
      '<link rel="icon" href="/favicon.ico"><link rel="icon" href="/icon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/apple-icon.png">';
    const logo = `${API}/api/v1/branding/assets/logo?v=abababababababab`;
    applyBrandingFavicon(logo);
    const icons = [...document.querySelectorAll<HTMLLinkElement>('link[rel~="icon"]')];
    expect(icons.map((link) => link.getAttribute('href'))).toEqual([logo]);
    expect(icons[0].id).toBe(BRANDING_ICON_ID);
    expect(document.querySelector('link[rel="apple-touch-icon"]')).not.toBeNull();

    applyBrandingFavicon(logo);
    expect(document.querySelectorAll('link[rel~="icon"]')).toHaveLength(1);

    applyBrandingFavicon(null);
    expect([...document.querySelectorAll('link[rel~="icon"]')].map((link) => link.getAttribute('href'))).toEqual(['/favicon.ico', '/icon.svg']);
    expect(document.getElementById(BRANDING_ICON_ID)).toBeNull();
  });
});
