/**
 * Admin branding (colours, logo, busy animation), configured in the Ingest
 * portal and served read-only by Weave-API's public `GET /v1/branding`.
 * This module is pure (no fetch, no React): it validates that response and
 * turns it into the two things the chat uses -- a `<style>` text built from
 * hex values only, and same-origin asset URLs for `<img>` and the favicon.
 * Anything that does not validate is dropped, which renders today's look.
 */

export const BRANDING_ASSET_KINDS = ['logo', 'busy'] as const;
export type BrandingAssetKind = (typeof BRANDING_ASSET_KINDS)[number];

export const BRANDING_CONTENT_TYPES = ['image/svg+xml', 'image/gif', 'image/png'] as const;
export type BrandingContentType = (typeof BRANDING_CONTENT_TYPES)[number];

export const BRANDING_EXTENSIONS: Record<BrandingContentType, string> = {
  'image/svg+xml': 'svg',
  'image/gif': 'gif',
  'image/png': 'png',
};

const COLOR_KEYS = ['accent', 'accent_hover', 'accent_ink', 'accent_soft', 'on_accent'] as const;
type ColorKey = (typeof COLOR_KEYS)[number];
export type BrandingColors = Record<ColorKey, string>;
export interface BrandingPalette {
  light: BrandingColors;
  dark: BrandingColors;
}

export interface BrandingAssetRef {
  contentType: BrandingContentType;
  /** Chat-local proxy URL: `/api/branding/assets/<kind>?v=<first 16 hex of sha256>`. */
  url: string;
}

export interface Branding {
  palette: BrandingPalette | null;
  logo: BrandingAssetRef | null;
  busy: BrandingAssetRef | null;
}

const HEX_COLOR = /^#[0-9a-f]{6}$/;
const SHA256 = /^[0-9a-f]{64}$/;

export function isBrandingAssetKind(value: string): value is BrandingAssetKind {
  return (BRANDING_ASSET_KINDS as readonly string[]).includes(value);
}

export function isBrandingContentType(value: string): value is BrandingContentType {
  return (BRANDING_CONTENT_TYPES as readonly string[]).includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validColors(value: unknown): BrandingColors | null {
  if (!isRecord(value)) return null;
  const colors = {} as BrandingColors;
  for (const key of COLOR_KEYS) {
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

function validAsset(kind: BrandingAssetKind, value: unknown): BrandingAssetRef | null {
  if (!isRecord(value)) return null;
  const { content_type: contentType, sha256 } = value;
  if (typeof contentType !== 'string' || !isBrandingContentType(contentType)) return null;
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) return null;
  return { contentType, url: `/api/branding/assets/${kind}?v=${sha256.slice(0, 16)}` };
}

/** Weave-API's `BrandingPublic` JSON -> `Branding`, or null when it is not
 * an object at all. Invalid parts are dropped one by one. */
export function parseBranding(body: unknown): Branding | null {
  if (!isRecord(body)) return null;
  return {
    palette: validPalette(body.palette),
    logo: validAsset('logo', body.logo),
    busy: validAsset('busy', body.busy),
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
 * globals.css's `:root` rules, so source order does not matter; the three
 * blocks mirror its light, manual-dark and system-dark rules.
 */
export function buildBrandingCss(palette: unknown): string | null {
  const valid = validPalette(palette);
  if (!valid) return null;
  const dark = declarations(valid.dark);
  return (
    `html:root{${declarations(valid.light)}}` +
    `html:root.dark{${dark}}` +
    `@media (prefers-color-scheme: dark){html:root:not(.light){${dark}}}`
  );
}
