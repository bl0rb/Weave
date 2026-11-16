'use client';

import { cn } from '@/lib/utils';
import { useCallback, useEffect, useRef, useState, type AnimationEvent } from 'react';

import { useBranding } from '@/components/branding-provider';
import { useWeaveMotion } from '@/lib/weave-motion';

type WeaveLogoProps = { className?: string; animation?: 'loop' | 'finite' };

/** How long 'finite' shows the custom busy asset: an `<img>` fires no animationend. */
const BRANDED_FINITE_MS = 4000;
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/**
 * The app's logo. Without admin branding it is exactly the built-in Weave
 * mark below, with its CSS animation. With a custom logo and/or busy
 * animation (components/branding-provider.tsx):
 * - no `animation`: the custom logo, else the built-in mark at rest;
 * - 'loop': the custom busy asset (never under prefers-reduced-motion),
 *   else the custom logo, else the animated built-in mark;
 * - 'finite': like 'loop' for four seconds, then like no `animation`.
 * Custom assets are always an `<img>`, never inlined markup.
 *
 * Reduced motion is left to the browser, so the server HTML is already
 * right and hydration never changes it: a `<picture>` whose reduced-motion
 * source is the logo, or -- without a logo -- the busy `<img>` and the
 * built-in mark side by side, one of them hidden by CSS.
 */
export function WeaveLogo({ className, animation }: WeaveLogoProps) {
  const branding = useBranding();
  // An asset that fails to load (e.g. the proxy answers 502 while Ingest is
  // down) counts as unset, so a broken image falls back to today's look.
  const [broken, setBroken] = useState<readonly string[]>([]);
  const markBroken = useCallback((src: string) => setBroken((list) => (list.includes(src) ? list : [...list, src])), []);
  const logoUrl = branding.logoUrl && !broken.includes(branding.logoUrl) ? branding.logoUrl : null;
  const busyUrl = branding.busyUrl && !broken.includes(branding.busyUrl) ? branding.busyUrl : null;
  const branded = Boolean(logoUrl || busyUrl);
  const finiteElapsed = useElapsed(branded && animation === 'finite', BRANDED_FINITE_MS);
  if (!branded) return <WeaveMark className={className} animation={animation} />;

  const moving = animation === 'loop' || (animation === 'finite' && !finiteElapsed);
  if (moving && busyUrl && logoUrl) {
    return <BrandingImage src={busyUrl} reducedMotionSrc={logoUrl} className={className} onBroken={markBroken} />;
  }
  if (moving && busyUrl) {
    return (
      <>
        <BrandingImage src={busyUrl} className={cn('motion-reduce:hidden', className)} onBroken={markBroken} />
        <WeaveMark className={cn('motion-safe:hidden', className)} animation={animation} />
      </>
    );
  }
  if (logoUrl) return <BrandingImage src={logoUrl} className={className} onBroken={markBroken} />;
  return <WeaveMark className={className} animation={moving ? animation : undefined} />;
}

/** A custom asset as `<img>`; with `reducedMotionSrc`, a `<picture>` that
 * shows that one instead under prefers-reduced-motion. */
function BrandingImage({
  src,
  reducedMotionSrc,
  className,
  onBroken,
}: {
  src: string;
  reducedMotionSrc?: string;
  className?: string;
  onBroken: (src: string) => void;
}) {
  const ref = useRef<HTMLImageElement>(null);
  // React does not replay an `error` that fired before hydration (the server
  // renders this, e.g. on /login), so check the state the request left the
  // element in. naturalWidth is also 0 for a loaded SVG without width and
  // height; only a broken image fails decode().
  useEffect(() => {
    const img = ref.current;
    if (!img?.complete || img.naturalWidth !== 0) return;
    let current = true;
    const report = () => {
      if (current) onBroken(shownSrc(src, reducedMotionSrc));
    };
    if (typeof img.decode === 'function') img.decode().catch(report);
    else report();
    return () => {
      current = false;
    };
  }, [src, reducedMotionSrc, onBroken]);
  const image = (
    // eslint-disable-next-line @next/next/no-img-element -- admin asset from the same-origin proxy; an <img> never runs an SVG's script
    <img ref={ref} src={src} alt="" aria-hidden="true" draggable={false} className={cn('object-contain', className)} onError={() => onBroken(shownSrc(src, reducedMotionSrc))} />
  );
  if (!reducedMotionSrc) return image;
  // `contents`: the <img> stays the box the caller's className lays out.
  return (
    <picture className="contents">
      <source media={REDUCED_MOTION_QUERY} srcSet={reducedMotionSrc} />
      {image}
    </picture>
  );
}

/** The URL a BrandingImage shows right now: the browser picks the picture's
 * source by the same media query. */
function shownSrc(src: string, reducedMotionSrc: string | undefined): string {
  const reduced = typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches;
  return reducedMotionSrc && reduced ? reducedMotionSrc : src;
}

/** True once `ms` have passed since `active` last turned true; false while inactive. */
function useElapsed(active: boolean, ms: number): boolean {
  const [state, setState] = useState({ active, elapsed: false });
  if (state.active !== active) setState({ active, elapsed: false });
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => setState({ active: true, elapsed: true }), ms);
    return () => window.clearTimeout(timer);
  }, [active, ms]);
  return active && state.elapsed;
}

/**
 * The Weave mark is a small woven swatch. Alternating crossings make the
 * over-under structure readable even at favicon size without forming a figure.
 *
 * Same mark and animation as the portal's weave-ingest-logo.tsx.
 * `animation` (CSS in app/globals.css, `.weave-logo`): 'loop' keeps weaving
 * while the assistant is working, 'finite' weaves five rounds and rests.
 * Both fall back to the static mark under prefers-reduced-motion.
 */
function WeaveMark({ className, animation }: WeaveLogoProps) {
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
