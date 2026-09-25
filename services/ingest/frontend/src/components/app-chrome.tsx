'use client';

import { useState } from 'react';
import { usePathname } from 'next/navigation';
import { AuthProvider, useAuth } from '@/lib/auth-context';
import { SidebarNav } from '@/components/sidebar-nav';
import { Topbar } from '@/components/topbar';
import { useI18n } from '@/i18n/provider';

const AUTH_PAGES = ['/login', '/setup'];

function isAuthPage(pathname: string): boolean {
  return AUTH_PAGES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** Sidebar + topbar + auth-gated content for protected routes. */
function ProtectedShell({ children }: { children: React.ReactNode }) {
  const { loading } = useAuth();
  const { t } = useI18n();
  // Shared with <Topbar>'s burger button, which lives outside the drawer.
  const [navOpen, setNavOpen] = useState(false);

  if (loading) {
    return (
      <div className="flex min-h-screen flex-1 items-center justify-center">
        <div
          role="status"
          aria-label={t('portal.chrome.loading')}
          className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-emerald-600"
        />
      </div>
    );
  }

  return (
    <>
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
