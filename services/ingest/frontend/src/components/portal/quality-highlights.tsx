'use client';

import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import type { QualityFinding, QualityFindingKind } from '@/lib/portal';

/** Findings show at most this many entries in the release panel before "show all". */
const LIST_LIMIT = 10;
export const findingAnchor = (lineIndex: number) => `finding-line-${lineIndex + 1}`;
const kindKey = (kind: QualityFindingKind) => `portal.jobDetail.finding.${kind}` as MessageKey;

export type MatchedFinding = { finding: QualityFinding; anchor: string | null };
/**
 * Locates findings in the portal markdown by their text, not by line number: the portal
 * rewrites the frontmatter, so the backend's line numbers shift. Findings that can't be
 * located stay in the list (anchor null).
 */
export function matchFindings(markdown: string, findings: QualityFinding[]): { lines: string[]; byLine: Map<number, QualityFinding[]>; matched: MatchedFinding[] } {
  const lines = markdown.split('\n');
  const byLine = new Map<number, QualityFinding[]>();
  const matched = findings.map(finding => {
    const needle = finding.text.split('\n').map(part => part.trim()).find(Boolean) ?? '';
    const hits = needle ? lines.flatMap((line, index) => line.includes(needle) ? [index] : []) : [];
    // Identical texts (repeated lines) take successive hits instead of all piling on the first.
    const index = hits.find(hit => !byLine.get(hit)?.some(other => other.text === finding.text)) ?? hits[0];
    if (index === undefined) return { finding, anchor: null };
    byLine.set(index, [...(byLine.get(index) ?? []), finding]);
    return { finding, anchor: findingAnchor(index) };
  });
  return { lines, byLine, matched };
}

/** Line view of the markdown with the lines of matching findings marked (marker + text, not colour alone). */
export function HighlightedMarkdown({ markdown, findings }: { markdown: string; findings: QualityFinding[] }) {
  const { t } = useI18n();
  const { lines, byLine, matched } = useMemo(() => matchFindings(markdown, findings), [markdown, findings]);
  const counts = findings.reduce<Partial<Record<QualityFindingKind, number>>>((sum, finding) => ({ ...sum, [finding.kind]: (sum[finding.kind] ?? 0) + 1 }), {});
  const unmatched = matched.filter(entry => !entry.anchor);
  return <div>
    <ul className="mb-3 flex flex-wrap gap-2">{(Object.keys(counts) as QualityFindingKind[]).map(kind => <li key={kind} className="portal-badge portal-badge-warning">{t(kindKey(kind))}: {counts[kind]}</li>)}</ul>
    <div role="region" aria-label={t('portal.reviews.highlight.viewAria')} className="overflow-x-auto font-mono text-[13px] leading-6">{lines.map((line, index) => {
      const hits = byLine.get(index);
      const label = hits ? t('portal.reviews.highlight.marker', { kind: [...new Set(hits.map(hit => t(kindKey(hit.kind))))].join(', ') }) : undefined;
      return <div key={index} id={hits ? findingAnchor(index) : undefined} title={label} className={`flex gap-2 scroll-mt-24 ${hits ? 'border-l-4 border-[var(--warn)] bg-[var(--warn-bg)] text-[var(--ink)]' : 'border-l-4 border-transparent'}`}>
        <span className="w-10 shrink-0 select-none text-right text-[var(--muted)]" aria-hidden="true">{index + 1}</span>
        <span className="w-5 shrink-0 text-[var(--warn)]">{hits && <AlertTriangle size={14} className="mt-1" aria-label={label} role="img" />}</span>
        <span className="whitespace-pre-wrap break-words">{line || ' '}</span>
        {hits && <span className="ml-auto shrink-0 pr-2 text-[12px] font-sans font-semibold text-[var(--warn)]">{label}</span>}
      </div>;
    })}</div>
    {unmatched.length > 0 && <div className="mt-3"><h3 className="text-[14px] font-semibold">{t('portal.reviews.highlight.unmatched')}</h3><ul className="font-mono text-[13px]">{unmatched.map((entry, index) => <li key={index}>{t(kindKey(entry.finding.kind))}: {entry.finding.text}</li>)}</ul></div>}
  </div>;
}

/** "n Auffälligkeiten" in the release panel; each entry jumps to its line (and switches highlighting on). */
export function FindingsList({ markdown, findings, onJump }: { markdown: string; findings: QualityFinding[]; onJump: (anchor: string | null) => void }) {
  const { t } = useI18n();
  const [all, setAll] = useState(false);
  const { matched } = useMemo(() => matchFindings(markdown, findings), [markdown, findings]);
  if (!matched.length) return null;
  const shown = all ? matched : matched.slice(0, LIST_LIMIT);
  return <section aria-labelledby="findings-list-title" className="grid gap-2">
    <h3 id="findings-list-title" className="text-[14px] font-semibold">{t('portal.reviews.highlight.listHeading', { count: matched.length })}</h3>
    <ul className="grid gap-1 text-[13px]">{shown.map((entry, index) => <li key={index}>
      <button type="button" className="w-full rounded px-2 py-1 text-left hover:bg-[var(--surface-2)]" aria-label={`${t(kindKey(entry.finding.kind))}: ${entry.finding.text.trim().slice(0, 80)}. ${t('portal.reviews.highlight.jump')}`} onClick={() => onJump(entry.anchor)}>
        <strong>{t(kindKey(entry.finding.kind))}</strong>{entry.anchor ? '' : ` (${t('portal.reviews.highlight.unmatched')})`}
        <span className="block truncate font-mono text-[12px] text-[var(--muted)]">{entry.finding.text.trim().slice(0, 80)}</span>
      </button>
    </li>)}</ul>
    {matched.length > LIST_LIMIT && <Button type="button" variant="ghost" size="sm" onClick={() => setAll(value => !value)}>{all ? t('portal.reviews.highlight.showLess') : t('portal.reviews.highlight.showAll', { count: matched.length })}</Button>}
  </section>;
}
