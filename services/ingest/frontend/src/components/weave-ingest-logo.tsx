/**
 * The Weave mark is a small woven swatch. Alternating crossings make the
 * over-under structure readable even at favicon size without forming a figure.
 */
export function WeaveIngestLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="15" fill="#0f8a64" />
      <g fill="#bfe7d4">
        <rect x="12" y="8" width="8" height="48" rx="4" />
        <rect x="28" y="8" width="8" height="48" rx="4" />
        <rect x="44" y="8" width="8" height="48" rx="4" />
      </g>
      <g fill="#f7fcf9">
        <rect x="8" y="12" width="48" height="8" rx="4" />
        <rect x="8" y="28" width="48" height="8" rx="4" />
        <rect x="8" y="44" width="48" height="8" rx="4" />
      </g>
      <g fill="#bfe7d4">
        <rect x="12" y="12" width="8" height="8" />
        <rect x="44" y="12" width="8" height="8" />
        <rect x="28" y="28" width="8" height="8" />
        <rect x="12" y="44" width="8" height="8" />
        <rect x="44" y="44" width="8" height="8" />
      </g>
      <g stroke="#0b6f52" strokeWidth="1" opacity=".28">
        <path d="M12 11.5h8M12 20.5h8M44 11.5h8M44 20.5h8" />
        <path d="M28 27.5h8M28 36.5h8" />
        <path d="M12 43.5h8M12 52.5h8M44 43.5h8M44 52.5h8" />
      </g>
    </svg>
  );
}
