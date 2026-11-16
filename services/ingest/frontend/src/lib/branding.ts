/**
 * Admin branding (colours, logo, busy animation), configured under
 * Administration › Betrieb › Erscheinungsbild and read from the public
 * `GET /api/v1/branding`. This module is pure (no fetch, no React): it
 * validates that response and turns it into what the portal uses -- a
 * `<style>` text built from hex values only, and asset URLs on the API
 * origin for `<img>` and the favicon. Anything that does not validate is
 * dropped, which renders today's look.
 */

export const BRANDING_PATH = '/api/v1/branding';
export const BRANDING_STORAGE_KEY = 'weave-ingest-branding';
export const BRANDING_STYLE_ID = 'weave-branding';
export const BRANDING_ICON_ID = 'weave-branding-icon';
/** The built-in primary colour (`--accent` in globals.css). */
export const DEFAULT_PRIMARY_COLOR = '#1d6a53';

export const BRANDING_ASSET_KINDS = ['logo', 'busy'] as const;
export type BrandingAssetKind = (typeof BRANDING_ASSET_KINDS)[number];

const CONTENT_TYPES = ['image/svg+xml', 'image/gif', 'image/png'] as const;
export type BrandingContentType = (typeof CONTENT_TYPES)[number];

export const BRANDING_COLOR_KEYS = ['accent', 'accent_hover', 'accent_ink', 'accent_soft', 'on_accent'] as const;
export type BrandingColorKey = (typeof BRANDING_COLOR_KEYS)[number];
export type BrandingColors = Record<BrandingColorKey, string>;
export interface BrandingPalette {
  light: BrandingColors;
  dark: BrandingColors;
}

export interface BrandingAsset {
  contentType: BrandingContentType;
  /** Absolute URL on the API origin, versioned by the content hash. */
  url: string;
}

export interface Branding {
  palette: BrandingPalette | null;
  logo: BrandingAsset | null;
  busy: BrandingAsset | null;
}

/** What the portal applied last; kept in localStorage for the next page load. */
export interface CachedBranding {
  css: string | null;
  logoUrl: string | null;
  busyUrl: string | null;
}

const HEX_COLOR = /^#[0-9a-f]{6}$/;
const HEX_INPUT = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
const SHA256 = /^[0-9a-f]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** `#abc` / `#AABBCC` -> lowercase `#aabbcc` (the backend's own
 * normalisation); null for anything else. */
export function normalizeHexColor(value: string): string | null {
  const trimmed = value.trim();
  if (!HEX_INPUT.test(trimmed)) return null;
  const hex = trimmed.slice(1).toLowerCase();
  return hex.length === 3 ? `#${hex[0]}${hex[0]}${hex[1]}${hex[1]}${hex[2]}${hex[2]}` : `#${hex}`;
}

function validColors(value: unknown): BrandingColors | null {
  if (!isRecord(value)) return null;
  const colors = {} as BrandingColors;
  for (const key of BRANDING_COLOR_KEYS) {
    const color = value[key];
    if (typeof color !== 'string' || !HEX_COLOR.test(color)) return null;
    colors[key] = color;
  }
  return colors;
}

/** The palette only when both sets are complete and every value is a
 * lowercase `#rrggbb`; null otherwise (never a partial palette). */
export function validPalette(value: unknown): BrandingPalette | null {
  if (!isRecord(value)) return null;
  const light = validColors(value.light);
  const dark = validColors(value.dark);
  return light && dark ? { light, dark } : null;
}

/** The asset URL is built here from kind and hash (never taken from the
 * response), relative to the API base like every other API path. */
export function brandingAssetUrl(apiBase: string, kind: BrandingAssetKind, sha256: string): string {
  return `${apiBase}${BRANDING_PATH}/assets/${kind}?v=${sha256.slice(0, 16)}`;
}

function validAsset(kind: BrandingAssetKind, value: unknown, apiBase: string): BrandingAsset | null {
  if (!isRecord(value)) return null;
  const { content_type: contentType, sha256 } = value;
  if (typeof contentType !== 'string' || !(CONTENT_TYPES as readonly string[]).includes(contentType)) return null;
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) return null;
  return { contentType: contentType as BrandingContentType, url: brandingAssetUrl(apiBase, kind, sha256) };
}

/** `BrandingPublic` (or `BrandingAdmin`) JSON -> `Branding`, or null when it
 * is not an object at all. Invalid parts are dropped one by one. */
export function parseBranding(body: unknown, apiBase: string): Branding | null {
  if (!isRecord(body)) return null;
  return {
    palette: validPalette(body.palette),
    logo: validAsset('logo', body.logo, apiBase),
    busy: validAsset('busy', body.busy, apiBase),
  };
}

function declarations(colors: BrandingColors): string {
  return (
    `--accent:${colors.accent};--accent-hover:${colors.accent_hover};--accent-ink:${colors.accent_ink};` +
    `--accent-soft:${colors.accent_soft};--on-accent:${colors.on_accent}`
  );
}

/**
 * The `<style id="weave-branding">` text, or null to inject nothing. Built
 * from validated hex values and fixed selectors only. `html:root` outranks
 * globals.css's `:root` / `:root.dark` rules, so source order does not
 * matter. The portal has no system-dark CSS rule (the init script in
 * layout.tsx always sets `.light` or `.dark`), so two blocks suffice.
 */
export function buildBrandingCss(palette: unknown): string | null {
  const valid = validPalette(palette);
  if (!valid) return null;
  return `html:root{${declarations(valid.light)}}html:root.dark{${declarations(valid.dark)}}`;
}

// Exactly the shape buildBrandingCss produces. The cache (and the init
// script in layout.tsx) accept nothing else, so a tampered localStorage
// entry can never inject anything but five hex colours per mode.
const CSS_DECLARATIONS = BRANDING_COLOR_KEYS.map((key) => `--${key.replace('_', '-')}:#[0-9a-f]{6}`).join(';');
export const BRANDING_CSS_PATTERN = new RegExp(`^html:root\\{${CSS_DECLARATIONS}\\}html:root\\.dark\\{${CSS_DECLARATIONS}\\}$`);

export function isBrandingCss(value: unknown): value is string {
  return typeof value === 'string' && BRANDING_CSS_PATTERN.test(value);
}

export function brandingToCache(branding: Branding | null): CachedBranding {
  return {
    css: buildBrandingCss(branding?.palette ?? null),
    logoUrl: branding?.logo?.url ?? null,
    busyUrl: branding?.busy?.url ?? null,
  };
}

function cachedAssetUrl(value: unknown, apiBase: string, kind: BrandingAssetKind): string | null {
  if (typeof value !== 'string') return null;
  const prefix = `${apiBase}${BRANDING_PATH}/assets/${kind}?v=`;
  return value.startsWith(prefix) && /^[0-9a-f]{16}$/.test(value.slice(prefix.length)) ? value : null;
}

/** The localStorage entry, validated again; null when absent or unusable.
 * Asset URLs must point at the current API base. */
export function readCachedBranding(raw: string | null, apiBase: string): CachedBranding | null {
  if (!raw) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value)) return null;
  const cached = {
    css: isBrandingCss(value.css) ? value.css : null,
    logoUrl: cachedAssetUrl(value.logoUrl, apiBase, 'logo'),
    busyUrl: cachedAssetUrl(value.busyUrl, apiBase, 'busy'),
  };
  return cached.css || cached.logoUrl || cached.busyUrl ? cached : null;
}

/**
 * Appended to the theme init script in layout.tsx: applies the cached
 * colours before the first paint. Same validation as {@link isBrandingCss};
 * the page then loads the current branding (components/branding-provider.tsx).
 */
export const BRANDING_INIT_SCRIPT = `
(function () {
  try {
    var cached = JSON.parse(window.localStorage.getItem(${JSON.stringify(BRANDING_STORAGE_KEY)}) || 'null');
    var css = cached && cached.css;
    if (typeof css === 'string' && new RegExp(${JSON.stringify(BRANDING_CSS_PATTERN.source)}).test(css)) {
      var style = document.createElement('style');
      style.id = ${JSON.stringify(BRANDING_STYLE_ID)};
      style.textContent = css;
      document.head.appendChild(style);
    }
  } catch (_) {}
})();
`;

/** Inject, replace or remove `<style id="weave-branding">`; only text that
 * passes {@link isBrandingCss} is ever injected. */
export function applyBrandingCss(css: string | null): void {
  const existing = document.getElementById(BRANDING_STYLE_ID);
  if (!isBrandingCss(css)) {
    existing?.remove();
    return;
  }
  let style = existing instanceof HTMLStyleElement ? existing : null;
  if (!style) {
    existing?.remove();
    style = document.createElement('style');
    style.id = BRANDING_STYLE_ID;
    document.head.appendChild(style);
  }
  if (style.textContent !== css) style.textContent = css;
}

const PARKED_REL = 'data-weave-icon-rel';

/**
 * Point the browser-tab icon at the custom logo, or restore the built-in
 * icons (app/icon.svg, favicon.ico) for null. The built-in `<link rel="icon">`
 * elements are parked (rel renamed, original kept in a data attribute) while
 * the custom icon is active, so the browser has only one icon to choose.
 */
export function applyBrandingFavicon(url: string | null): void {
  const own = document.getElementById(BRANDING_ICON_ID);
  if (!url) {
    own?.remove();
    document.querySelectorAll<HTMLLinkElement>(`link[${PARKED_REL}]`).forEach((link) => {
      link.setAttribute('rel', link.getAttribute(PARKED_REL) ?? 'icon');
      link.removeAttribute(PARKED_REL);
    });
    return;
  }
  document.querySelectorAll<HTMLLinkElement>(`link[rel~="icon"]:not(#${BRANDING_ICON_ID})`).forEach((link) => {
    link.setAttribute(PARKED_REL, link.getAttribute('rel') ?? 'icon');
    link.setAttribute('rel', 'weave-parked-icon');
  });
  let link = own instanceof HTMLLinkElement ? own : null;
  if (!link) {
    own?.remove();
    link = document.createElement('link');
    link.id = BRANDING_ICON_ID;
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  if (link.getAttribute('href') !== url) link.setAttribute('href', url);
}
