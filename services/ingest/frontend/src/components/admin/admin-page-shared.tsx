'use client';

import { useEffect, useRef, useState } from 'react';
import type { ComponentType, KeyboardEvent, ReactNode } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { ArrowRight, ShieldAlert } from 'lucide-react';

import { apiJson } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { useI18n } from '@/i18n/provider';

/** Page head used by every /admin/* route: h1 + one-line description, optional actions. */
export function PageHead({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold leading-tight text-[var(--ink)]">{title}</h1>
          <p className="mt-1 text-[15px] text-[var(--muted)]">{description}</p>
        </div>
        {actions}
      </div>
    </header>
  );
}

/** Shared container + admin-only gate for every /admin/* route (identical to the previous single-page gate). */
export function AdminPageShell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const { t } = useI18n();

  if (!user || user.role !== 'admin') {
    return (
      <main className="min-h-screen">
        <div className="mx-auto flex w-full max-w-7xl flex-col items-center px-4 py-24 sm:px-6 lg:px-8">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100">
            <ShieldAlert className="h-6 w-6 text-slate-400" />
          </div>
          <h1 className="mt-4 text-[17px] font-semibold text-slate-950">{t('admin.shared.adminsOnlyTitle')}</h1>
          <p className="mt-1 max-w-md text-center text-sm text-slate-500">{t('admin.shared.adminsOnlyBody')}</p>
        </div>
      </main>
    );
  }

  // Same container (max-width + padding) as the portal side — see
  // .portal-page in globals.css, shared by both areas of the app.
  return (
    <main id="main-content" className="portal-page">
      {children}
    </main>
  );
}

export type SectionTabDef<T extends string> = { id: T; label: string; icon?: ComponentType<{ className?: string }> };

/**
 * Compact in-page segmented control (ARIA tabs pattern, mirrors the app's
 * existing tablist markup in connections/page.tsx) for a page with several
 * heavy sections.
 */
export function SectionTabs<T extends string>({
  idPrefix,
  ariaLabel,
  tabs,
  active,
  onChange,
}: {
  idPrefix: string;
  ariaLabel: string;
  tabs: SectionTabDef<T>[];
  active: T;
  onChange: (id: T) => void;
}) {
  const ids = tabs.map((t) => t.id);
  const refs = useRef<Partial<Record<string, HTMLButtonElement | null>>>({});

  const focusTab = (id: T) => {
    onChange(id);
    refs.current[id]?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, id: T) => {
    const index = ids.indexOf(id);
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      focusTab(ids[(index + 1) % ids.length]);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      focusTab(ids[(index - 1 + ids.length) % ids.length]);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusTab(ids[0]);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusTab(ids[ids.length - 1]);
    }
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="mb-6 inline-flex flex-wrap gap-1 rounded-lg border border-[var(--line)] bg-[var(--surface)] p-1 shadow-sm"
    >
      {tabs.map(({ id, label, icon: Icon }) => {
        const isActive = active === id;
        return (
          <button
            key={id}
            ref={(el) => {
              refs.current[id] = el;
            }}
            id={`${idPrefix}-tab-${id}`}
            role="tab"
            aria-selected={isActive}
            aria-controls={`${idPrefix}-panel-${id}`}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(id)}
            onKeyDown={(event) => onKeyDown(event, id)}
            className={`flex h-10 items-center gap-2 rounded-lg px-4 text-sm font-semibold transition ${
              isActive ? 'bg-emerald-50 text-emerald-800' : 'text-[var(--ink-2)] hover:bg-[var(--hover)] hover:text-[var(--ink)]'
            }`}
          >
            {Icon && <Icon className={`h-4 w-4 ${isActive ? 'text-emerald-700' : 'text-[var(--muted)]'}`} />}
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function SectionPanel({ idPrefix, id, children }: { idPrefix: string; id: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`${idPrefix}-panel-${id}`} aria-labelledby={`${idPrefix}-tab-${id}`}>
      {children}
    </div>
  );
}

/**
 * Reads/writes the active section of a segmented-control page from the
 * `?bereich=` query param, so old-tab redirects and bookmarks land on the
 * right section. Falls back to `fallback` for a missing/invalid value.
 *
 * The active id is kept in local state (seeded from the URL on first
 * render) rather than re-derived from `searchParams` on every render —
 * `router.replace` only updates the query string, and mirroring it back
 * into local state keeps the segmented control responsive even when a
 * client-side navigation doesn't immediately re-run this hook.
 */
export function useBereich<T extends string>(validIds: readonly T[], fallback: T): [T, (id: T) => void] {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const raw = searchParams.get('bereich');
  const initial = raw !== null && (validIds as readonly string[]).includes(raw) ? (raw as T) : fallback;
  const [active, setActive] = useState<T>(initial);

  const onChange = (id: T) => {
    setActive(id);
    const params = new URLSearchParams(searchParams.toString());
    params.set('bereich', id);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  return [active, onChange];
}

/** Old `/admin?tab=<id>` values → the new page (and section) that now hosts them. */
export const OLD_TAB_REDIRECTS: Record<string, { path: string; bereich?: string }> = {
  collections: { path: '/admin/menschen', bereich: 'zugriff' },
  users: { path: '/admin/menschen', bereich: 'personen' },
  teams: { path: '/admin/menschen', bereich: 'teams' },
  providers: { path: '/admin/menschen', bereich: 'anmeldung' },
  bots: { path: '/admin/wissen', bereich: 'bots' },
  'chat-provider': { path: '/admin/wissen', bereich: 'chat-llm' },
  'retrieval-provider': { path: '/admin/wissen', bereich: 'suche-modelle' },
  'vl-connections': { path: '/admin/verarbeitung', bereich: 'dokument-ki' },
  paddle: { path: '/admin/verarbeitung', bereich: 'ocr' },
  logs: { path: '/admin/verarbeitung', bereich: 'worker-logs' },
  backup: { path: '/admin/betrieb', bereich: 'sicherung' },
  'technical-identities': { path: '/admin/betrieb', bereich: 'identitaeten' },
  tools: { path: '/admin/betrieb', bereich: 'werkzeuge' },
};

/** Resolves an old `?tab=` value to the new URL, or null if it isn't one of the old 13 tab keys. */
export function oldTabRedirectUrl(tab: string): string | null {
  const target = OLD_TAB_REDIRECTS[tab];
  if (!target) return null;
  return target.bereich ? `${target.path}?bereich=${target.bereich}` : target.path;
}

/** Small link card used for "further tools" grids on the admin sub-pages. */
export function ToolLinkCard({
  href,
  title,
  description,
}: {
  href: string;
  title: string;
  description: string;
}) {
  return (
    <Link href={href} className="rounded-xl border border-[var(--line)] bg-[var(--surface)] p-5 transition hover:border-emerald-400">
      <span className="flex items-center justify-between gap-3 font-semibold text-emerald-800">
        {title}
        <ArrowRight size={16} />
      </span>
      <p className="mt-2 text-sm text-[var(--muted)]">{description}</p>
    </Link>
  );
}

/**
 * Fetches a single JSON resource once on mount. Errors (including 404s for
 * not-yet-deployed endpoints) are swallowed — `data` simply stays null, so
 * callers treat a missing signal as "not available" rather than an error to
 * surface, matching the Übersicht page's no-fake-data rule.
 */
export function useAdminJson<T>(path: string): { data: T | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiJson<T>(path)
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch(() => {
        // Signal simply omitted — see doc comment above.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  return { data, loading };
}
