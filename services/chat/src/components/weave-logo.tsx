import { cn } from '@/lib/utils';

/**
 * The Weave mark is a small woven swatch. Alternating crossings make the
 * over-under structure readable even at favicon size without forming a figure.
 *
 * Same mark and animation as the portal's weave-ingest-logo.tsx.
 * `animation` (CSS in app/globals.css, `.weave-logo`): 'intro' weaves the
 * weft rows in once, 'loop' keeps weaving while the assistant is working.
 * Both fall back to the static mark under prefers-reduced-motion.
 */
export function WeaveLogo({ className, animation }: { className?: string; animation?: 'intro' | 'loop' }) {
  return (
    <svg viewBox="0 0 64 64" className={cn('weave-logo', animation && `weave-logo--${animation}`, className)} aria-hidden="true">
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
