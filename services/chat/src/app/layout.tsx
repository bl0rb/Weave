import type { Metadata } from 'next';
import { Source_Sans_3 } from 'next/font/google';
import { I18nProvider } from '@/i18n/provider';
import { getLocale, getTranslator } from '@/i18n/server';
import './globals.css';

// Same font as the portal (services/ingest/frontend/src/app/layout.tsx),
// loaded the same way via next/font so both apps render with it self-hosted
// rather than a render-blocking Google Fonts request.
const sourceSans = Source_Sans_3({
  subsets: ['latin'],
  variable: '--font-sans',
});

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getTranslator();
  return { title: t('common.appTitle'), description: t('common.appDescription') };
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
  const locale = await getLocale();
  return (
    <html lang={locale} className={`h-full ${sourceSans.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="h-full antialiased">
        <I18nProvider initialLocale={locale}>{children}</I18nProvider>
      </body>
    </html>
  );
}
