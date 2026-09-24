'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  ChevronDown,
  FolderOpen,
  Home,
  KeyRound,
  ListChecks,
  LogOut,
  MessageSquare,
  ArrowLeft,
  ArrowUpRight,
  FileText,
  HelpCircle,
  Inbox,
  Lock,
  Server,
  ShieldCheck,
  UsersRound,
} from 'lucide-react';
import { useAuth } from '@/lib/auth-context';
import { WeaveIngestLogo } from '@/components/weave-ingest-logo';
import { ThemeToggle } from '@/components/theme-toggle';
import { resolveChatPublicUrl } from '@/lib/api-base';
import { loadDocuments } from '@/lib/portal';
import { loadFailedJobs } from '@/lib/jobs-search';

type NavItem = { href: string; label: string; icon: typeof Home; badge?: number };

const PORTAL_NAV: Omit<NavItem, 'badge'>[] = [
  { href: '/', label: 'Übersicht', icon: Home },
  { href: '/aufgaben', label: 'Aufgaben', icon: ListChecks },
  { href: '/knowledge', label: 'Wissensbereiche', icon: FolderOpen },
  { href: '/documents', label: 'Dokumente', icon: FileText },
];

const ADMIN_NAV: NavItem[] = [
  { href: '/admin', label: 'Übersicht', icon: Home },
  { href: '/admin/menschen', label: 'Menschen & Zugriffe', icon: UsersRound },
  { href: '/admin/wissen', label: 'Wissen & Assistenten', icon: FolderOpen },
  { href: '/admin/verarbeitung', label: 'Verarbeitung & Quellen', icon: Inbox },
  { href: '/admin/betrieb', label: 'Betrieb & Sicherheit', icon: Server },
];

function isActive(href: string, pathname: string): boolean {
  return href === '/' || href === '/admin' ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
}

export function SidebarNav({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const pathname = usePathname();
  const drawerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const { user, logout } = useAuth();
  const [helpOpen, setHelpOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [tasksBadge, setTasksBadge] = useState<number | null>(null);
  // Lazy initializer (not an effect): resolveChatPublicUrl() only ever reads
  // a value injected before hydration (see runtime-env.js) and never
  // changes during the page's lifetime.
  const [chatUrl] = useState<string | null>(() => resolveChatPublicUrl());
  const isAdminArea = pathname.startsWith('/admin');
  const navItems: NavItem[] = isAdminArea ? ADMIN_NAV : PORTAL_NAV.map((item) => (item.href === '/aufgaben' ? { ...item, badge: tasksBadge ?? undefined } : item));

  // Aufgaben badge = open reviews + failed jobs. Both requests are as cheap
  // as the APIs allow (limit 0/1, only `total` is read) and are refreshed on
  // navigation so finishing a review or a retry clears the badge reasonably
  // promptly without a polling loop.
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    Promise.all([loadDocuments(undefined, 0, 'review', undefined, 1), loadFailedJobs(0)])
      .then(([reviews, failed]) => { if (!cancelled) setTasksBadge(reviews.total + failed.total); })
      .catch(() => { if (!cancelled) setTasksBadge(null); });
    return () => { cancelled = true; };
  }, [user, pathname]);

  useEffect(() => {
    function onPointerDown(e: PointerEvent) {
      if (open && drawerRef.current && !drawerRef.current.contains(e.target as Node)) onOpenChange(false);
      if (menuOpen && menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open, menuOpen, onOpenChange]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      onOpenChange(false);
      setMenuOpen(false);
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onOpenChange]);

  return (
    <>
      {open && <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" aria-hidden="true" onClick={() => onOpenChange(false)} />}

      <div
        ref={drawerRef}
        className={`fixed left-0 top-0 z-40 flex h-full w-64 flex-col gap-3.5 overflow-y-auto border-r border-[var(--line)] bg-[var(--surface)] p-3.5 shadow-2xl transition-transform duration-200 lg:translate-x-0 lg:shadow-none ${open ? 'translate-x-0' : '-translate-x-full'}`}
      >
        <Link href={isAdminArea ? '/admin' : '/'} onClick={() => onOpenChange(false)} className="flex items-center gap-2.5 px-1.5 pb-1.5 pt-0.5 text-[var(--ink)] no-underline">
          <WeaveIngestLogo className="h-8 w-8 flex-shrink-0" />
          <span>
            <span className="block text-[19px] font-bold leading-tight tracking-tight">Weave</span>
            <span className="block text-[11.5px] font-medium text-[var(--muted)]">{isAdminArea ? 'Administration' : 'Wissensportal'}</span>
          </span>
        </Link>

        <div className="flex items-center gap-2.5 rounded-[10px] border border-[var(--line)] bg-[var(--surface-2)] px-2.5 py-2 text-[var(--muted)]">
          <span className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-lg bg-[var(--ink)] text-[11.5px] font-bold text-white">WV</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13.5px] font-semibold text-[var(--ink)]">Weave Wissensportal</span>
            <span className="block truncate text-xs">{isAdminArea ? 'Administration' : user ? `Angemeldet als ${user.username}` : 'Interner Wissensraum'}</span>
          </span>
        </div>

        {!isAdminArea && chatUrl && (
          <a
            href={chatUrl}
            target="_blank"
            rel="noreferrer"
            className="flex min-h-[42px] items-center gap-2.5 rounded-[10px] bg-[var(--accent)] px-3 text-[13.5px] font-semibold text-[var(--on-accent)] no-underline transition hover:bg-[var(--accent-hover)]"
          >
            <MessageSquare className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            <span className="flex-1">Chat öffnen</span>
            <ArrowUpRight className="h-[15px] w-[15px] flex-shrink-0 opacity-80" aria-hidden="true" />
          </a>
        )}

        <nav aria-label={isAdminArea ? 'Administration' : 'Hauptnavigation'} className="flex flex-1 flex-col gap-0.5">
          {navItems.map(({ href, label, icon: Icon, badge }) => {
            const active = isActive(href, pathname);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? 'page' : undefined}
                onClick={() => onOpenChange(false)}
                className="shell-nav-link flex min-h-10 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13.5px] font-medium text-[var(--ink-2)] no-underline transition hover:bg-[var(--hover)] hover:text-[var(--ink)]"
              >
                <Icon className="h-[18px] w-[18px] flex-shrink-0 text-[var(--muted)]" aria-hidden="true" />
                {label}
                {Boolean(badge) && (
                  <span className="ml-auto inline-grid h-5 min-w-[22px] place-items-center rounded-full bg-[var(--warn-bg)] px-1.5 text-[11.5px] font-bold text-[var(--warn)]">
                    {badge}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto grid gap-2.5">
          {isAdminArea ? (
            <Link href="/" onClick={() => onOpenChange(false)} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] font-medium text-[var(--ink-2)] no-underline hover:bg-[var(--hover)]">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Zum Arbeitsplatz
            </Link>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setHelpOpen((v) => !v)}
                aria-expanded={helpOpen}
                className="flex min-h-10 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13.5px] font-medium text-[var(--ink-2)] transition hover:bg-[var(--hover)] hover:text-[var(--ink)]"
              >
                <HelpCircle className="h-[18px] w-[18px] flex-shrink-0 text-[var(--muted)]" aria-hidden="true" />
                Hilfe & Orientierung
              </button>
              {helpOpen && (
                <p className="-mt-1.5 px-2.5 pb-0.5 text-xs leading-relaxed text-[var(--muted)]">
                  So funktioniert Weave: Quelle hinzufügen → automatisch verarbeiten → du prüfst und gibst frei → Indexierung → im Chat verfügbar für alle mit Zugriff.
                </p>
              )}
              <p className="flex gap-2 rounded-[10px] bg-[var(--surface-2)] p-2.5 text-xs leading-relaxed text-[var(--muted)]">
                <Lock className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--accent)]" aria-hidden="true" />
                Läuft in eurer Infrastruktur. Inhalte verlassen dein Unternehmen nicht.
              </p>
            </>
          )}

          {user && (
            <div ref={menuRef} className="relative border-t border-[var(--line)] pt-2.5">
              {menuOpen && (
                <div role="menu" className="shell-profile-menu">
                  <Link role="menuitem" href="/settings" onClick={() => { setMenuOpen(false); onOpenChange(false); }} className="flex min-h-10 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13.5px] font-medium text-[var(--ink-2)] no-underline hover:bg-[var(--hover)] hover:text-[var(--ink)]">
                    <KeyRound className="h-4 w-4 text-[var(--muted)]" aria-hidden="true" />
                    API-Zugänge
                  </Link>
                  <div role="menuitem" className="flex min-h-10 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13.5px] font-medium text-[var(--ink-2)]">
                    <ThemeToggle />
                    Farbschema
                  </div>
                  {user.role === 'admin' && (
                    <Link role="menuitem" href="/admin" onClick={() => { setMenuOpen(false); onOpenChange(false); }} className="flex min-h-10 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13.5px] font-medium text-[var(--ink-2)] no-underline hover:bg-[var(--hover)] hover:text-[var(--ink)]">
                      <ShieldCheck className="h-4 w-4 text-[var(--muted)]" aria-hidden="true" />
                      Administration
                    </Link>
                  )}
                  <button type="button" role="menuitem" onClick={() => void logout()} className="flex min-h-10 w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13.5px] font-medium text-[var(--ink-2)] hover:bg-[var(--hover)] hover:text-[var(--ink)]">
                    <LogOut className="h-4 w-4 text-[var(--muted)]" aria-hidden="true" />
                    Abmelden
                  </button>
                </div>
              )}
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                className="flex w-full items-center gap-2.5 rounded-lg px-1.5 py-1 text-left transition hover:bg-[var(--hover)]"
              >
                <span className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-full bg-[var(--accent-soft)] text-xs font-bold text-[var(--accent-ink)]">
                  {user.username.slice(0, 2).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-semibold text-[var(--ink)]">{user.username}</span>
                  <span className="block text-[11.5px] uppercase tracking-wide text-[var(--muted)]">{user.role === 'admin' ? 'Administrator' : 'Mitglied'}</span>
                </span>
                <ChevronDown className={`h-4 w-4 flex-shrink-0 text-[var(--muted)] transition-transform ${menuOpen ? 'rotate-180' : ''}`} aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
