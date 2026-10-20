import { useEffect } from 'react';

/**
 * While an answer streams, the browser tab shows it too: the title gets an
 * ellipsis prefix and the favicon alternates between the full mark, the warp
 * alone and the weft alone -- a two-frame "weaving" visible from a
 * background tab. Both are restored when the work ends.
 */
const MARK_WARP =
  '<g fill="#bfe7d4"><rect x="12" y="8" width="8" height="48" rx="4"/><rect x="28" y="8" width="8" height="48" rx="4"/><rect x="44" y="8" width="8" height="48" rx="4"/></g>';
const MARK_WEFT =
  '<g fill="#f7fcf9"><rect x="8" y="12" width="48" height="8" rx="4"/><rect x="8" y="28" width="48" height="8" rx="4"/><rect x="8" y="44" width="48" height="8" rx="4"/></g>';
const MARK_KNOTS =
  '<g fill="#bfe7d4"><rect x="12" y="12" width="8" height="8"/><rect x="44" y="12" width="8" height="8"/><rect x="28" y="28" width="8" height="8"/><rect x="12" y="44" width="8" height="8"/><rect x="44" y="44" width="8" height="8"/></g>';

function frame(body: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="15" fill="#0f8a64"/>${body}</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export const BUSY_FAVICON_FRAMES = [frame(MARK_WARP + MARK_WEFT + MARK_KNOTS), frame(MARK_WARP), frame(MARK_WARP + MARK_WEFT)];
export const BUSY_TITLE_PREFIX = '… ';
const FRAME_MS = 700;

export function useBusyTabIndicator(busy: boolean): void {
  useEffect(() => {
    if (!busy) return;
    const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
    const originalHref = link?.getAttribute('href');
    const originalTitle = document.title;
    document.title = `${BUSY_TITLE_PREFIX}${originalTitle}`;
    let index = 0;
    const timer = window.setInterval(() => {
      index = (index + 1) % BUSY_FAVICON_FRAMES.length;
      link?.setAttribute('href', BUSY_FAVICON_FRAMES[index]);
    }, FRAME_MS);
    return () => {
      window.clearInterval(timer);
      if (link && originalHref !== null && originalHref !== undefined) link.setAttribute('href', originalHref);
      // Only strip our own prefix: a title changed meanwhile stays as is.
      if (document.title.startsWith(BUSY_TITLE_PREFIX)) document.title = document.title.slice(BUSY_TITLE_PREFIX.length);
    };
  }, [busy]);
}
