'use client';

import { useState, type FormEvent } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { FilePlus, Menu, Search } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/** Longest-prefix-wins route → breadcrumb label. Extend when a new top-level route is added. */
const ROUTE_LABELS: [string, string][] = [
  ['/knowledge/new', 'Wissensbereich anlegen'],
  ['/knowledge', 'Wissensbereiche'],
  ['/documents', 'Dokumente'],
  ['/aufgaben', 'Aufgaben'],
  ['/reviews', 'Prüfen & freigeben'],
  ['/processing', 'Verarbeitung'],
  ['/imports', 'Confluence-Importe'],
  ['/jobs', 'Aufträge'],
  ['/sources/new', 'Quelle hinzufügen'],
  ['/settings', 'API-Zugänge'],
  ['/connections', 'Verbindungen'],
  ['/benchmark', 'Qualität vergleichen'],
  ['/search', 'Suche'],
  ['/mail', 'Mail'],
  ['/chat', 'Chat'],
  ['/admin', 'Administration'],
];

function pageTitle(pathname: string): string {
  if (pathname === '/') return 'Übersicht';
  const matches = ROUTE_LABELS.filter(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  return matches.sort((a, b) => b[0].length - a[0].length)[0]?.[1] ?? 'Weave';
}

/**
 * App-wide topbar: mobile burger, breadcrumb, document search
 * (Enter → /documents?q=) and the primary "Quelle hinzufügen" CTA.
 * The burger toggles the drawer owned by <SidebarNav>; both live under
 * <AppChrome>, which holds the shared open/close state.
 */
export function Topbar({ onMenuClick }: { onMenuClick: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const [query, setQuery] = useState('');

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const trimmed = query.trim();
    router.push(trimmed ? `/documents?q=${encodeURIComponent(trimmed)}` : '/documents');
  }

  return (
    <header className="sticky top-0 z-20 flex min-h-[62px] items-center gap-3 border-b border-[var(--line)] bg-[var(--bg)] px-4 sm:px-8">
      <button
        type="button"
        onClick={onMenuClick}
        aria-label="Navigation öffnen"
        className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-[var(--line-2)] bg-[var(--surface)] text-[var(--ink)] transition hover:bg-[var(--hover)] lg:hidden"
      >
        <Menu className="h-4 w-4" aria-hidden="true" />
      </button>

      <p className="hidden min-w-0 truncate text-sm text-[var(--muted)] md:block">
        Weave <span aria-hidden="true">/</span> <strong className="font-semibold text-[var(--ink)]">{pageTitle(pathname)}</strong>
      </p>

      <form role="search" onSubmit={submitSearch} className="ml-auto flex h-10 w-full max-w-[200px] items-center gap-2 rounded-lg border border-[var(--line-2)] bg-[var(--surface)] px-3 text-[var(--muted)] transition focus-within:border-[var(--accent)] sm:max-w-[320px]">
        <Search className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <label className="sr-only" htmlFor="topbar-search">Dokumente durchsuchen</label>
        <input
          id="topbar-search"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Dokumente durchsuchen"
          className="w-full min-w-0 border-0 bg-transparent text-sm text-[var(--ink)] outline-none placeholder:text-[var(--muted)]"
        />
      </form>

      <Link href="/sources/new" className={cn(buttonVariants({ size: 'sm' }), 'flex-shrink-0')}>
        <FilePlus className="h-4 w-4" aria-hidden="true" />
        <span className="hidden sm:inline">Quelle hinzufügen</span>
      </Link>
    </header>
  );
}
