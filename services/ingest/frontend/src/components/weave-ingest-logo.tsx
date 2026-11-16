'use client';

import { cn } from '@/lib/utils';
import { useEffect, useState, type AnimationEvent } from 'react';

import { useBranding } from '@/components/branding-provider';
import { usePrefersReducedMotion } from '@/lib/reduced-motion';
import { useWeaveMotion } from '@/lib/weave-motion';

type LogoAnimation = 'loop' | 'finite';

/**
 * The built-in Weave mark is a small woven swatch. Alternating crossings make
 * the over-under structure readable even at favicon size without forming a
 * figure. Kept in sync with packages/weave-mark (scripts/check_weave_mark.py).
 *
 * `animation` (CSS in globals.css, `.weave-logo`): 'loop' keeps weaving as a
 * busy indicator, 'finite' weaves five rounds and rests. Both fall back to
 * the static mark under prefers-reduced-motion.
 */
export function WeaveIngestMark({ className, animation }: { className?: string; animation?: LogoAnimation }) {
  // One of four motions (weave / turn / flip / shuttle), picked when the
  // animation starts and kept until it stops -- see lib/weave-motion.ts. A
  // click on the mark switches to the next motion (and, when idle, runs one
  // short round of it); the last round ends with a soft settle.
  const { motion, kicked, cycle, settle } = useWeaveMotion(Boolean(animation));
  const mode = animation ?? (kicked ? 'finite' : undefined);
  // The settle belongs to one run (mode + motion): it stays applied once that
  // run has ended -- removing it would restart the run's own animation -- and
  // simply stops matching when the next run starts.
  const runKey = mode && motion ? `${mode}-${motion}` : null;
  const [settledRun, setSettledRun] = useState<string | null>(null);
  const onAnimationEnd = (event: AnimationEvent<SVGSVGElement>) => {
    if (event.animationName === 'weave-settle' || mode !== 'finite') return;
    setSettledRun(runKey);
    settle();
  };
  const settling = settledRun !== null && settledRun === runKey;
  return (
    <svg
      viewBox="0 0 64 64"
      className={cn(
        'weave-logo',
        mode && `weave-logo--${mode}`,
        motion && `weave-logo--motion-${motion}`,
        settling && 'weave-logo--settle',
        className,
      )}
      aria-hidden="true"
      onClick={cycle}
      onAnimationEnd={onAnimationEnd}
    >
      <rect width="64" height="64" rx="15" fill="#0f8a64" />
      <g fill="#bfe7d4">
        <rect x="12" y="8" width="8" height="48" rx="4" />
        <rect x="28" y="8" width="8" height="48" rx="4" />
        <rect x="44" y="8" width="8" height="48" rx="4" />
      </g>
      <g fill="#f7fcf9" className="weave-weft">
        <rect data-row="0" x="8" y="12" width="48" height="8" rx="4" />
        <rect data-row="1" x="8" y="28" width="48" height="8" rx="4" />
        <rect data-row="2" x="8" y="44" width="48" height="8" rx="4" />
      </g>
      <g fill="#bfe7d4" className="weave-knots">
        <rect data-row="0" x="12" y="12" width="8" height="8" />
        <rect data-row="0" x="44" y="12" width="8" height="8" />
        <rect data-row="1" x="28" y="28" width="8" height="8" />
        <rect data-row="2" x="12" y="44" width="8" height="8" />
        <rect data-row="2" x="44" y="44" width="8" height="8" />
      </g>
      <g stroke="#0b6f52" strokeWidth="1" opacity=".28" className="weave-knots">
        <path data-row="0" d="M12 11.5h8M12 20.5h8M44 11.5h8M44 20.5h8" />
        <path data-row="1" d="M28 27.5h8M28 36.5h8" />
        <path data-row="2" d="M12 43.5h8M12 52.5h8M44 43.5h8M44 52.5h8" />
      </g>
    </svg>
  );
}

/** How long 'finite' shows the custom busy asset before it rests (an `<img>`
 * fires no animation events, so the built-in mark's rounds cannot be counted). */
export const BRANDING_FINITE_MS = 4000;

/**
 * An admin-uploaded asset (components/branding-provider.tsx). Always an
 * `<img>`: an uploaded SVG is never inlined, so its markup cannot reach the
 * page's DOM.
 */
export function BrandingAssetImage({ src, className, onError }: { src: string; className?: string; onError?: () => void }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- admin asset on the API origin, known only at runtime; next/image cannot be configured for it
    <img src={src} alt="" aria-hidden="true" draggable={false} className={cn('object-contain', className)} onError={onError} />
  );
}

/**
 * The logo wherever the portal shows it. Without admin branding it is
 * exactly the built-in mark ({@link WeaveIngestMark}). With a custom logo
 * and/or busy animation:
 * - no `animation`: the custom logo, else the built-in mark at rest;
 * - 'loop': the busy asset (unless the user prefers reduced motion), else
 *   the custom logo, else the animated built-in mark;
 * - 'finite': like 'loop' for {@link BRANDING_FINITE_MS}, then at rest.
 */
export function WeaveIngestLogo({ className, animation }: { className?: string; animation?: LogoAnimation }) {
  const branding = useBranding();
  // An asset that fails to load (removed since the cached URL, unreachable)
  // counts as unset, so the logo falls back to today's look instead of a gap.
  const [broken, setBroken] = useState<readonly string[]>([]);
  const usable = (url: string | null) => (url && !broken.includes(url) ? url : null);
  const logoUrl = usable(branding.logoUrl);
  const busyUrl = usable(branding.busyUrl);
  const markBroken = (url: string) => () => setBroken((current) => (current.includes(url) ? current : [...current, url]));
  const reducedMotion = usePrefersReducedMotion();
  const custom = Boolean(logoUrl || busyUrl);
  const finiteRun = custom && animation === 'finite';
  const [finiteDone, setFiniteDone] = useState(false);
  const [trackedRun, setTrackedRun] = useState(finiteRun);
  if (trackedRun !== finiteRun) {
    // A new finite run starts busy again (the "adjust state while rendering" pattern).
    setTrackedRun(finiteRun);
    setFiniteDone(false);
  }
  useEffect(() => {
    if (!finiteRun) return;
    const timer = window.setTimeout(() => setFiniteDone(true), BRANDING_FINITE_MS);
    return () => window.clearTimeout(timer);
  }, [finiteRun]);

  if (!custom) return <WeaveIngestMark className={className} animation={animation} />;
  const busy = animation === 'loop' || (animation === 'finite' && !finiteDone);
  if (busy && busyUrl && !reducedMotion) return <BrandingAssetImage src={busyUrl} className={className} onError={markBroken(busyUrl)} />;
  if (logoUrl) return <BrandingAssetImage src={logoUrl} className={className} onError={markBroken(logoUrl)} />;
  return <WeaveIngestMark className={className} animation={busy ? animation : undefined} />;
}
