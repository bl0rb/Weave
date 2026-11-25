'use client';

import { Suspense, useState } from 'react';
import { usePathname } from 'next/navigation';
import { Lock, LogOut } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AuthProvider, useAuth } from '@/lib/auth-context';
import { SidebarNav } from '@/components/sidebar-nav';
import { Topbar } from '@/components/topbar';
import { useNavigationRecorder } from '@/lib/navigation-history';
import { useI18n } from '@/i18n/provider';

const AUTH_PAGES = ['/login', '/setup'];

function isAuthPage(pathname: string): boolean {
  return AUTH_PAGES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** Renders nothing; isolated so useSearchParams' Suspense bailout cannot blank the app. */
function NavigationRecorder() {
  useNavigationRecorder();
  return null;
}

/** Sidebar + topbar + auth-gated content for protected routes. */
function ProtectedShell({ children }: { children: React.ReactNode }) {
  const { loading, user, logout } = useAuth();
  const { t } = useI18n();
  // Shared with <Topbar>'s burger button, which lives outside the drawer.
  const [navOpen, setNavOpen] = useState(false);

  if (loading) {
    return (
      <div className="flex min-h-screen flex-1 items-center justify-center">
        <div
          role="status"
          aria-label={t('portal.chrome.loading')}
          className="brand-spinner h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-emerald-600"
        />
      </div>
    );
  }

  // A non-admin without any knowledge-space grant: the backend answers 403
  // on every portal route (deps.require_knowledge_access), so say why
  // instead of rendering a shell full of failing requests.
  if (user && user.role !== 'admin' && user.knowledge_access === false) {
    return (
      <main className="flex min-h-screen flex-1 items-center justify-center px-4">
        <div role="alert" className="w-full max-w-md rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-8 text-center shadow-sm">
          <Lock className="mx-auto h-8 w-8 text-[var(--muted)]" aria-hidden="true" />
          <h1 className="mt-4 text-lg font-semibold text-[var(--ink)]">{t('portal.chrome.noAccess.title')}</h1>
          <p className="mt-2 text-sm leading-relaxed text-[var(--muted)]">{t('portal.chrome.noAccess.text')}</p>
          <Button type="button" variant="outline" className="mt-6" onClick={() => void logout()}>
            <LogOut className="h-4 w-4" aria-hidden="true" />
            {t('portal.chrome.noAccess.logout')}
          </Button>
        </div>
      </main>
    );
  }

  return (
    <>
      <Suspense fallback={null}><NavigationRecorder /></Suspense>
      <SidebarNav open={navOpen} onOpenChange={setNavOpen} />
      <div className="lg:pl-64">
        <Topbar onMenuClick={() => setNavOpen(true)} />
        {children}
      </div>
    </>
  );
}

/**
 * Client-side app shell. Auth pages (/login, /setup) render bare —
 * no sidebar, no loading gate; everything else gets the sidebar and
 * waits for the initial session check before showing content.
 * AuthProvider wraps both branches (it skips redirects on auth pages
 * itself), so useAuth() works everywhere.
 */
export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <AuthProvider>
      {isAuthPage(pathname) ? children : <ProtectedShell>{children}</ProtectedShell>}
    </AuthProvider>
  );
}
