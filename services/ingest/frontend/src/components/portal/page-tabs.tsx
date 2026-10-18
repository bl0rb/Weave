'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useI18n } from '@/i18n/provider';

type PageTab = { href: string; label: string };

/** Link-based tabs for sibling pages that share one sidebar entry (each tab
 * keeps its own URL, so bookmarks and deep links stay valid). Styled like the
 * admin SectionTabs. */
function PageTabs({ ariaLabel, tabs }: { ariaLabel: string; tabs: PageTab[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label={ariaLabel} className="mb-6">
      <div className="inline-flex flex-wrap gap-1 rounded-lg border border-[var(--line)] bg-[var(--surface)] p-1 shadow-sm">
        {tabs.map(({ href, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={`flex h-10 items-center rounded-lg px-4 text-sm font-semibold no-underline transition ${
                active ? 'bg-emerald-50 text-emerald-800' : 'text-[var(--ink-2)] hover:bg-[var(--hover)] hover:text-[var(--ink)]'
              }`}
            >
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

/** Aufgaben: overview, review inbox, processing status. */
export function TaskTabs() {
  const { t } = useI18n();
  return (
    <PageTabs
      ariaLabel={t('portal.nav.tasks')}
      tabs={[
        { href: '/aufgaben', label: t('portal.tabs.overview') },
        { href: '/reviews', label: t('portal.chrome.breadcrumb.reviews') },
        { href: '/processing', label: t('portal.chrome.breadcrumb.processing') },
      ]}
    />
  );
}

/** Dokumente: portal list and the advanced job management. */
export function DocumentTabs() {
  const { t } = useI18n();
  return (
    <PageTabs
      ariaLabel={t('portal.nav.documents')}
      tabs={[
        { href: '/documents', label: t('portal.tabs.overview') },
        { href: '/jobs', label: t('portal.tabs.advanced') },
      ]}
    />
  );
}
