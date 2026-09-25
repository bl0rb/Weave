'use client';

import { useState, type FormEvent } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { FilePlus, Menu, Search } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { cn } from '@/lib/utils';

/** Longest-prefix-wins route → breadcrumb label. Extend when a new top-level route is added. */
const ROUTE_LABELS: [string, MessageKey][] = [
  ['/knowledge/new', 'portal.chrome.breadcrumb.knowledgeNew'],
  ['/knowledge', 'portal.nav.knowledgeSpaces'],
  ['/documents', 'portal.nav.documents'],
  ['/aufgaben', 'portal.nav.tasks'],
  ['/reviews', 'portal.chrome.breadcrumb.reviews'],
  ['/processing', 'portal.chrome.breadcrumb.processing'],
  ['/imports', 'portal.chrome.breadcrumb.imports'],
  ['/jobs', 'portal.chrome.breadcrumb.jobs'],
  ['/sources/new', 'portal.chrome.breadcrumb.sourcesNew'],
  ['/settings', 'portal.chrome.apiTokens'],
  ['/connections', 'portal.chrome.breadcrumb.connections'],
  ['/benchmark', 'portal.chrome.breadcrumb.benchmark'],
  ['/search', 'portal.chrome.breadcrumb.search'],
  ['/mail', 'portal.chrome.breadcrumb.mail'],
  ['/chat', 'portal.chrome.breadcrumb.chat'],
  ['/admin', 'portal.chrome.adminSubtitle'],
];

function pageTitle(pathname: string, t: (key: MessageKey) => string): string {
  if (pathname === '/') return t('portal.nav.overview');
  const matches = ROUTE_LABELS.filter(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  const key = matches.sort((a, b) => b[0].length - a[0].length)[0]?.[1];
  return key ? t(key) : 'Weave';
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
  const { t } = useI18n();
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
        aria-label={t('portal.chrome.menuOpen')}
        className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-[var(--line-2)] bg-[var(--surface)] text-[var(--ink)] transition hover:bg-[var(--hover)] lg:hidden"
      >
        <Menu className="h-4 w-4" aria-hidden="true" />
      </button>

      <p className="hidden min-w-0 truncate text-sm text-[var(--muted)] md:block">
        Weave <span aria-hidden="true">/</span> <strong className="font-semibold text-[var(--ink)]">{pageTitle(pathname, t)}</strong>
      </p>

      <form role="search" onSubmit={submitSearch} className="ml-auto flex h-10 w-full max-w-[200px] items-center gap-2 rounded-lg border border-[var(--line-2)] bg-[var(--surface)] px-3 text-[var(--muted)] transition focus-within:border-[var(--accent)] sm:max-w-[320px]">
        <Search className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <label className="sr-only" htmlFor="topbar-search">{t('portal.chrome.searchDocuments')}</label>
        <input
          id="topbar-search"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('portal.chrome.searchDocuments')}
          className="w-full min-w-0 border-0 bg-transparent text-sm text-[var(--ink)] outline-none placeholder:text-[var(--muted)]"
        />
      </form>

      <Link href="/sources/new" className={cn(buttonVariants({ size: 'sm' }), 'flex-shrink-0')}>
        <FilePlus className="h-4 w-4" aria-hidden="true" />
        <span className="hidden sm:inline">{t('portal.chrome.addSource')}</span>
      </Link>
    </header>
  );
}
