import type { Metadata } from 'next';
import { Source_Sans_3 } from 'next/font/google';
import { cache } from 'react';
import { BrandingProvider } from '@/components/branding-provider';
import { I18nProvider } from '@/i18n/provider';
import { getLocale, getTranslator } from '@/i18n/server';
import { buildBrandingCss } from '@/lib/branding';
import { fetchBranding } from '@/lib/weave-api-server';
import './globals.css';

// Same font as the portal (services/ingest/frontend/src/app/layout.tsx),
// loaded the same way via next/font so both apps render with it self-hosted
// rather than a render-blocking Google Fonts request.
const sourceSans = Source_Sans_3({
  subsets: ['latin'],
  variable: '--font-sans',
});

// Admin branding (lib/branding.ts), fetched once per request for both the
// metadata and the layout; null (today's look) when Weave-API does not
// answer within 1.5 s or answers nothing valid.
const loadBranding = cache(fetchBranding);

export async function generateMetadata(): Promise<Metadata> {
  const [{ t }, branding] = await Promise.all([getTranslator(), loadBranding()]);
  const metadata: Metadata = { title: t('common.appTitle'), description: t('common.appDescription') };
  // A custom logo becomes the tab icon. Config `icons` replace the
  // app/icon.svg file icon (Next only adds file icons when no `icons` were
  // resolved), so without a custom logo the built-in mark stays.
  if (branding?.logo) metadata.icons = { icon: [{ url: branding.logo.url, type: branding.logo.contentType }] };
  return metadata;
}

// Inline, pre-hydration script: applies a previously chosen theme (stored
// in localStorage by theme-toggle.tsx) before first paint, so there is no
// flash of the wrong theme. Guarded top-to-bottom against a private
// window / disabled storage — falls through to the CSS-only
// prefers-color-scheme default in globals.css when it can't read
// anything.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = window.localStorage.getItem('weave-chat-theme');
    if (stored === 'light' || stored === 'dark') {
      document.documentElement.classList.add(stored);
    }
  } catch (_) {}
})();
`;

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const [locale, branding] = await Promise.all([getLocale(), loadBranding()]);
  const brandingCss = buildBrandingCss(branding?.palette);
  return (
    <html lang={locale} className={`h-full ${sourceSans.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/* Built from validated hex values and fixed selectors only (buildBrandingCss). */}
        {brandingCss ? <style id="weave-branding" dangerouslySetInnerHTML={{ __html: brandingCss }} /> : null}
      </head>
      <body className="h-full antialiased">
        <I18nProvider initialLocale={locale}>
          <BrandingProvider logoUrl={branding?.logo?.url ?? null} busyUrl={branding?.busy?.url ?? null}>
            {children}
          </BrandingProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
