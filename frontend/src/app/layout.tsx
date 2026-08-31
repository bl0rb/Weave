import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Weave Chat',
  description: 'Chat-Oberfläche für die Weave-Bots — spricht ausschließlich mit Weave-API.',
};

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

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="de" className="h-full">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="h-full antialiased">{children}</body>
    </html>
  );
}
