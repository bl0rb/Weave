'use client';

import { createContext, useContext, useMemo, type ReactNode } from 'react';

/** The admin's custom assets as same-origin URLs (lib/branding.ts), null when unset. */
export interface BrandingAssets {
  logoUrl: string | null;
  busyUrl: string | null;
}

// Without a provider (unit tests, isolated renders) nothing is custom, so
// every consumer renders today's look.
const BrandingContext = createContext<BrandingAssets>({ logoUrl: null, busyUrl: null });

/** Set once by the root layout from its server-side branding fetch. */
export function BrandingProvider({ logoUrl, busyUrl, children }: BrandingAssets & { children: ReactNode }) {
  const value = useMemo(() => ({ logoUrl, busyUrl }), [logoUrl, busyUrl]);
  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>;
}

export function useBranding(): BrandingAssets {
  return useContext(BrandingContext);
}
