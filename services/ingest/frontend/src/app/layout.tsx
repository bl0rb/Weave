import type { Metadata } from "next";
import { Lora, Source_Sans_3 } from 'next/font/google';
import Script from 'next/script';
import { AppChrome } from '@/components/app-chrome';
import { BrandingProvider } from '@/components/branding-provider';
import { I18nProvider } from '@/i18n/provider';
import { getLocale, getLocalePreference, getTranslator } from '@/i18n/server';
import { BRANDING_INIT_SCRIPT } from '@/lib/branding';
import "./globals.css";

const sourceSans = Source_Sans_3({
  subsets: ['latin'],
  variable: '--font-sans',
});

const lora = Lora({
  subsets: ['latin'],
  variable: '--font-serif',
});

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getTranslator();
  return { title: t('common.appTitle'), description: t('common.appDescription') };
}

// Before the first paint: the theme class, then the cached admin branding
// colours (lib/branding.ts; validated again there).
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = window.localStorage.getItem('weave-ingest-theme');
    var theme = stored === 'light' || stored === 'dark'
      ? stored
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.classList.add(theme);
  } catch (_) {}
})();
${BRANDING_INIT_SCRIPT}`;

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const [locale, preference] = await Promise.all([getLocale(), getLocalePreference()]);
  return (
    <html lang={locale} className={`h-full antialiased ${sourceSans.variable} ${lora.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/*
          `beforeInteractive` is Next.js's blessed way to run a script
          synchronously, before hydration/any client bundle executes —
          it's injected into the initial HTML and executed first, same
          effect as a plain synchronous <script> in <head> but without
          tripping the no-sync-scripts lint rule. Served by
          src/app/runtime-env.js/route.ts, which reads process.env live
          on every request under `next start` — this is what makes the
          backend API URL configurable at container start instead of
          being baked into the build.
        */}
        <Script src="/runtime-env.js" strategy="beforeInteractive" />
      </head>
      <body className="min-h-full flex flex-col">
        <I18nProvider initialLocale={locale} initialPreference={preference}>
          <BrandingProvider>
            <AppChrome>{children}</AppChrome>
          </BrandingProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
