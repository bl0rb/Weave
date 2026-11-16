'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { apiJson } from '@/lib/api';
import { resolveApiBaseUrl } from '@/lib/api-base';
import {
  BRANDING_PATH,
  BRANDING_STORAGE_KEY,
  applyBrandingCss,
  applyBrandingFavicon,
  brandingToCache,
  parseBranding,
  readCachedBranding,
  type CachedBranding,
} from '@/lib/branding';

export interface BrandingState {
  /** The admin's custom assets as absolute URLs on the API origin, null when unset. */
  logoUrl: string | null;
  busyUrl: string | null;
  /**
   * Load the current branding and apply it (colours, favicon, assets).
   * `data` -- a `BrandingPublic`/`BrandingAdmin` body the caller already
   * holds (the admin tab's save response) -- is applied without a request,
   * so the portal restyles at once even while another backend process
   * still serves its cached copy.
   */
  refresh: (data?: unknown) => Promise<void>;
}

// Without a provider (unit tests, isolated renders) nothing is custom, so
// every consumer renders today's look.
const BrandingContext = createContext<BrandingState>({ logoUrl: null, busyUrl: null, refresh: async () => {} });

const LOAD_TIMEOUT_MS = 10_000;

function writeCache(cached: CachedBranding): void {
  try {
    if (cached.css || cached.logoUrl || cached.busyUrl) {
      window.localStorage.setItem(BRANDING_STORAGE_KEY, JSON.stringify(cached));
    } else {
      window.localStorage.removeItem(BRANDING_STORAGE_KEY);
    }
  } catch {
    // Storage unavailable (private mode, quota): the next load just starts plain.
  }
}

function readCache(): CachedBranding | null {
  try {
    return readCachedBranding(window.localStorage.getItem(BRANDING_STORAGE_KEY), resolveApiBaseUrl());
  } catch {
    return null;
  }
}

async function loadBranding(): Promise<unknown> {
  const signal = typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(LOAD_TIMEOUT_MS) : undefined;
  // Public route: a 401 here must never send the user to /login.
  return apiJson<unknown>(BRANDING_PATH, { skipAuthRedirect: true, cache: 'no-store', signal });
}

/**
 * Loads the admin branding once per page load and applies it: the
 * `<style id="weave-branding">` colour overrides, the favicon and the
 * logo/busy asset URLs for `WeaveIngestLogo`. The colours of the previous
 * visit are already in place before paint (init script in layout.tsx). If
 * the branding cannot be loaded or does not validate, today's look is
 * restored.
 */
export function BrandingProvider({ children }: { children: ReactNode }) {
  const [assets, setAssets] = useState<{ logoUrl: string | null; busyUrl: string | null }>({ logoUrl: null, busyUrl: null });
  const latest = useRef(0);
  // The custom tab icon currently applied (null = the built-in icons).
  const iconUrl = useRef<string | null>(null);
  const applyIcon = useCallback((url: string | null) => {
    iconUrl.current = url;
    applyBrandingFavicon(url);
  }, []);

  useEffect(() => {
    // Next remounts its metadata (keyed per request) on every client-side
    // navigation, which adds fresh built-in `<link rel="icon">` elements next
    // to the custom one. Park them again whenever the head changes.
    if (typeof MutationObserver === 'undefined') return;
    const observer = new MutationObserver(() => {
      if (iconUrl.current) applyBrandingFavicon(iconUrl.current);
    });
    observer.observe(document.head, { childList: true });
    return () => observer.disconnect();
  }, []);

  const refresh = useCallback(async (data?: unknown) => {
    const request = ++latest.current;
    let cached: CachedBranding;
    try {
      const body = data === undefined ? await loadBranding() : data;
      cached = brandingToCache(parseBranding(body, resolveApiBaseUrl()));
    } catch {
      cached = brandingToCache(null);
    }
    // A newer refresh (e.g. after an admin save) wins over a slower older one.
    if (request !== latest.current) return;
    applyBrandingCss(cached.css);
    applyIcon(cached.logoUrl);
    writeCache(cached);
    setAssets({ logoUrl: cached.logoUrl, busyUrl: cached.busyUrl });
  }, [applyIcon]);

  useEffect(() => {
    // The previous visit's assets until the current branding arrives, so
    // the logo does not flash back to the built-in mark on every load.
    // Read here, not in the initial state: the server renders without them.
    const cached = readCache();
    if (cached) {
      applyIcon(cached.logoUrl);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time sync from localStorage after hydration
      setAssets({ logoUrl: cached.logoUrl, busyUrl: cached.busyUrl });
    }
    void refresh();
  }, [applyIcon, refresh]);

  const value = useMemo(() => ({ ...assets, refresh }), [assets, refresh]);
  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>;
}

export function useBranding(): BrandingState {
  return useContext(BrandingContext);
}
