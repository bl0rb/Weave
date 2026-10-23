'use client';

import { useEffect } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';

// Next.js exposes no "can I go back inside the app" signal, and
// window.history.length also counts entries from before the app (or other
// sites, or forward entries after a browser back). So every history entry the
// app creates carries its in-app depth in history.state: pushState stamps
// depth + 1, replaceState keeps the current depth. Browser back/forward and
// reloads restore the stamped state, so the depth always belongs to the entry
// actually shown, and a BackLink only does router.back() when depth > 0.
const DEPTH_KEY = '__weaveDepth';
const listeners = new Set<() => void>();
let installed = false;
let rawReplace: History['replaceState'] | null = null;

function depthOf(state: unknown): number {
  const value = state && typeof state === 'object' ? (state as Record<string, unknown>)[DEPTH_KEY] : undefined;
  return typeof value === 'number' ? value : 0;
}

function stamp(state: unknown, depth: number): unknown {
  return state && typeof state === 'object' ? { ...(state as object), [DEPTH_KEY]: depth } : { [DEPTH_KEY]: depth };
}

/** Wraps history.pushState/replaceState once so every entry carries its depth. */
function install(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  const { history } = window;
  const push = history.pushState.bind(history);
  const replace = rawReplace = history.replaceState.bind(history);
  history.pushState = (state, unused, url) => push(stamp(state, depthOf(history.state) + 1), unused, url);
  history.replaceState = (state, unused, url) => replace(stamp(state, depthOf(history.state)), unused, url);
}

/** Notifies BackLinks after the visible location changed; called from AppChrome. */
export function recordNavigation(): void {
  install();
  listeners.forEach(listener => listener());
}

/** True when the shown entry was pushed by the app, so router.back() stays inside it. */
export function canGoBackInApp(): boolean {
  if (typeof window === 'undefined') return false;
  return depthOf(window.history.state) > 0;
}

export function subscribeNavigation(listener: () => void): () => void {
  install();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Test helper: start from a fresh, app-less history entry. */
export function resetNavigationHistory(): void {
  install();
  rawReplace?.({ [DEPTH_KEY]: 0 }, '');
}

/** Mount once (AppChrome). Needs a Suspense boundary because of useSearchParams. */
export function useNavigationRecorder(): void {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  useEffect(() => {
    recordNavigation();
  }, [pathname, search]);
}
