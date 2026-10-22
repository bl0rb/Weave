'use client';

import { useSyncExternalStore } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { canGoBackInApp, subscribeNavigation } from '@/lib/navigation-history';
import { useI18n } from '@/i18n/provider';

/**
 * Back link that behaves like the browser's back button when there is an in-app
 * history entry, so in-page and browser back never diverge; otherwise (deep link,
 * new tab) it goes to the fixed fallback. The decision comes from a store with a `false`
 * server snapshot, so SSR and hydration both show the fallback (no mismatch).
 */
export function BackLink({ fallbackHref, fallbackLabel, className }: { fallbackHref: string; fallbackLabel: string; className?: string }) {
  const { t } = useI18n();
  const router = useRouter();
  const canGoBack = useSyncExternalStore(subscribeNavigation, canGoBackInApp, () => false);
  return (
    <Link
      href={fallbackHref}
      className={className}
      onClick={event => {
        if (!canGoBack) return;
        event.preventDefault();
        router.back();
      }}
    >
      {canGoBack ? t('common.back') : fallbackLabel}
    </Link>
  );
}
