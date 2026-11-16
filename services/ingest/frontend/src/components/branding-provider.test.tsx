// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiJson } from '@/lib/api';
import { resolveApiBaseUrl } from '@/lib/api-base';
import { BRANDING_ICON_ID, BRANDING_STORAGE_KEY, BRANDING_STYLE_ID, buildBrandingCss } from '@/lib/branding';
import { BrandingProvider, useBranding, type BrandingState } from './branding-provider';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);

const SHA = 'cd'.repeat(32);
const palette = {
  light: { accent: '#7a1f3d', accent_hover: '#681a34', accent_ink: '#5c172e', accent_soft: '#f1e6ea', on_accent: '#ffffff' },
  dark: { accent: '#d68aa3', accent_hover: '#dc9cb1', accent_ink: '#f0cfd9', accent_soft: '#3e2930', on_accent: '#0c1511' },
};
const body = {
  primary_color: '#7a1f3d', accent_color: null, palette,
  logo: { content_type: 'image/svg+xml', sha256: SHA, size_bytes: 120, url: `/api/v1/branding/assets/logo?v=${SHA.slice(0, 16)}` },
  busy: null, updated_at: '2026-11-16T10:00:00Z',
};

const probe: { current: BrandingState | null } = { current: null };
function Probe() {
  const value = useBranding();
  useEffect(() => { probe.current = value; });
  return <span data-testid="logo">{value.logoUrl ?? 'none'}</span>;
}
const state = () => probe.current!;

beforeEach(() => {
  api.mockReset();
  document.head.innerHTML = '<link rel="icon" href="/favicon.ico">';
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

it('loads the public branding without a login redirect and applies colours, favicon and assets', async () => {
  api.mockResolvedValue(body);
  render(<BrandingProvider><Probe /></BrandingProvider>);
  const logoUrl = `${resolveApiBaseUrl()}/api/v1/branding/assets/logo?v=${SHA.slice(0, 16)}`;
  await waitFor(() => expect(screen.getByTestId('logo').textContent).toBe(logoUrl));
  expect(api).toHaveBeenCalledWith('/api/v1/branding', expect.objectContaining({ skipAuthRedirect: true }));
  expect(document.getElementById(BRANDING_STYLE_ID)?.textContent).toBe(buildBrandingCss(palette));
  expect(document.getElementById(BRANDING_ICON_ID)?.getAttribute('href')).toBe(logoUrl);
  expect(state().busyUrl).toBeNull();
  expect(JSON.parse(window.localStorage.getItem(BRANDING_STORAGE_KEY) ?? 'null')).toEqual({ css: buildBrandingCss(palette), logoUrl, busyUrl: null });
});

it('injects nothing when the branding cannot be loaded, and drops the colours cached from the last visit', async () => {
  // As the init script in layout.tsx leaves it after a branded visit.
  const style = document.createElement('style');
  style.id = BRANDING_STYLE_ID;
  style.textContent = buildBrandingCss(palette);
  document.head.appendChild(style);
  window.localStorage.setItem(BRANDING_STORAGE_KEY, JSON.stringify({ css: buildBrandingCss(palette), logoUrl: null, busyUrl: null }));
  api.mockRejectedValue(new TypeError('Failed to fetch'));

  render(<BrandingProvider><Probe /></BrandingProvider>);
  await waitFor(() => expect(document.getElementById(BRANDING_STYLE_ID)).toBeNull());
  expect(screen.getByTestId('logo').textContent).toBe('none');
  expect(document.getElementById(BRANDING_ICON_ID)).toBeNull();
  expect(document.querySelector('link[rel="icon"]')?.getAttribute('href')).toBe('/favicon.ico');
  expect(window.localStorage.getItem(BRANDING_STORAGE_KEY)).toBeNull();
});

it('injects nothing for invalid data', async () => {
  api.mockResolvedValue({ ...body, palette: { ...palette, dark: { ...palette.dark, accent: 'red' } }, logo: { content_type: 'text/html', sha256: SHA } });
  render(<BrandingProvider><Probe /></BrandingProvider>);
  await waitFor(() => expect(api).toHaveBeenCalled());
  await act(async () => {});
  expect(document.getElementById(BRANDING_STYLE_ID)).toBeNull();
  expect(document.getElementById(BRANDING_ICON_ID)).toBeNull();
  expect(screen.getByTestId('logo').textContent).toBe('none');
});

it('refresh(data) applies a body the caller already holds without another request', async () => {
  api.mockResolvedValue({ ...body, palette: null, logo: null });
  render(<BrandingProvider><Probe /></BrandingProvider>);
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  await act(async () => { await state().refresh(body); });
  expect(api).toHaveBeenCalledTimes(1);
  expect(document.getElementById(BRANDING_STYLE_ID)?.textContent).toBe(buildBrandingCss(palette));
  expect(state().logoUrl).toContain('/api/v1/branding/assets/logo?v=');

  api.mockResolvedValue({ ...body, palette: null, logo: null });
  await act(async () => { await state().refresh(); });
  expect(api).toHaveBeenCalledTimes(2);
  expect(document.getElementById(BRANDING_STYLE_ID)).toBeNull();
  expect(state().logoUrl).toBeNull();
});

it('keeps the custom tab icon when a navigation brings back the built-in icon links', async () => {
  api.mockResolvedValue(body);
  render(<BrandingProvider><Probe /></BrandingProvider>);
  const logoUrl = `${resolveApiBaseUrl()}/api/v1/branding/assets/logo?v=${SHA.slice(0, 16)}`;
  await waitFor(() => expect(document.getElementById(BRANDING_ICON_ID)?.getAttribute('href')).toBe(logoUrl));

  // As Next's remounted metadata leaves the head after a client-side navigation.
  const fresh = document.createElement('link');
  fresh.rel = 'icon';
  fresh.href = '/icon.svg';
  document.head.appendChild(fresh);
  await waitFor(() => expect([...document.querySelectorAll('link[rel~="icon"]')].map(link => link.getAttribute('href'))).toEqual([logoUrl]));

  // Without a custom logo the built-in icons are left alone again.
  await act(async () => { await state().refresh({ ...body, logo: null }); });
  const later = document.createElement('link');
  later.rel = 'icon';
  later.href = '/later.svg';
  document.head.appendChild(later);
  await act(async () => {});
  expect([...document.querySelectorAll('link[rel~="icon"]')].map(link => link.getAttribute('href'))).toEqual(['/favicon.ico', '/icon.svg', '/later.svg']);
});
