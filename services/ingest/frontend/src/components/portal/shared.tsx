'use client';

import Link from 'next/link';
import { ArrowRight, Download, FileText, RefreshCw } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { dateLabel, documentState, documentUrl, type PortalDocument } from '@/lib/portal';
import { useIndexingStatus } from '@/lib/use-indexing-status';

export function PortalPage({ title, description, eyebrow = null, actions, children }: {
  title: string; description: string; eyebrow?: string | null; actions?: React.ReactNode; children: React.ReactNode;
}) {
  return <main id="main-content" className="portal-page"><header className="portal-header"><div>{eyebrow && <p className="portal-eyebrow">{eyebrow}</p>}<h1>{title}</h1><p className="portal-description">{description}</p></div>{actions && <div className="portal-actions">{actions}</div>}</header>{children}</main>;
}
export function Notice({ children, error = false, action }: { children: React.ReactNode; error?: boolean; action?: () => void }) {
  return <div className={`portal-notice ${error ? 'portal-notice-error' : ''}`} role={error ? 'alert' : 'status'}><span>{children}</span>{action && <Button variant="outline" size="sm" onClick={action}><RefreshCw size={14} />Erneut versuchen</Button>}</div>;
}
export function EmptyState({ title, children, href, action }: { title: string; children: React.ReactNode; href?: string; action?: string }) {
  return <div className="portal-empty"><FileText size={28} aria-hidden="true" /><h3>{title}</h3><p>{children}</p>{href && <Link className={buttonVariants({ variant: 'outline' })} href={href}>{action}<ArrowRight size={16} /></Link>}</div>;
}
export function DocumentTable({ documents, onDownloadMarkdown, downloadingId, selectedIds, onToggle, onToggleAll }: {
  documents: PortalDocument[];
  onDownloadMarkdown?: (document: PortalDocument) => void;
  downloadingId?: string | null;
  selectedIds?: Set<string>;
  onToggle?: (id: string) => void;
  onToggleAll?: (checked: boolean) => void;
}) {
  const { items } = useIndexingStatus(documents.filter(document => document.release).map(document => document.id));
  const allSelected = Boolean(onToggle) && documents.length > 0 && documents.every(document => selectedIds?.has(document.id));
  return <div className="portal-table-scroll"><table className="portal-table"><caption className="sr-only">Dokumente und ihr Verarbeitungs- und Freigabestatus</caption><thead><tr>{onToggle && <th scope="col"><input type="checkbox" aria-label="Alle auf dieser Seite auswählen" checked={allSelected} onChange={event => onToggleAll?.(event.target.checked)} /></th>}<th scope="col">Dokument</th><th scope="col">Wissensbereich</th><th scope="col">Stand</th><th scope="col">Hinzugefügt</th><th scope="col"><span className="sr-only">Öffnen</span></th></tr></thead><tbody>{documents.map(document => {
    const state = documentState(document, items[document.id]);
    return <tr key={document.id}>{onToggle && <td><input type="checkbox" aria-label={`${document.original_filename} auswählen`} checked={selectedIds?.has(document.id) ?? false} onChange={() => onToggle(document.id)} /></td>}<td><Link className="portal-document-link" href={documentUrl(document)}><FileText size={17} aria-hidden="true" /><span>{document.original_filename}</span></Link><span className="mt-1 block max-w-[300px] text-xs leading-5 text-slate-500">{document.source?.label}{document.source?.path ? `: ${document.source.path}` : ''}</span></td><td><Link href={`/knowledge/${document.collection_id}`}>{document.collection_name || 'Wissensbereich'}</Link></td><td><div aria-live="polite"><span className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>{state.hint && <span className="mt-2 block max-w-[300px] text-xs leading-5 text-slate-500">{state.hint}</span>}{document.status === 'FINISHED' && !document.release && document.quality_grade && <span className="mt-2 block text-xs text-slate-500">Qualitätsstufe {document.quality_grade}</span>}{document.release?.released_by && <span className="mt-2 block max-w-[300px] text-xs leading-5 text-slate-500">Freigegeben von {document.release.released_by}</span>}</div></td><td className="portal-date">{dateLabel(document.created_at)}</td><td><div className="portal-row-actions">{onDownloadMarkdown && document.status === 'FINISHED' && <button className="portal-open" type="button" disabled={downloadingId === document.id} onClick={() => onDownloadMarkdown(document)} aria-label={`${document.original_filename} als Markdown herunterladen`} title="Markdown herunterladen"><Download size={17} aria-hidden="true" /></button>}<Link className="portal-open" href={documentUrl(document)} aria-label={`${document.original_filename} öffnen`}><ArrowRight size={17} /></Link></div></td></tr>;
  })}</tbody></table></div>;
}

export function BulkActionBar({ count, onRelease, onSkip, onDelete, onClear, gradeCCount, acceptQualityWarnings, onAcceptQualityWarningsChange, releaseConfirmed, onReleaseConfirmedChange, busy }: {
  count: number;
  onRelease: () => void;
  onSkip: () => void;
  onDelete: () => void;
  onClear: () => void;
  gradeCCount: number;
  acceptQualityWarnings: boolean;
  onAcceptQualityWarningsChange: (value: boolean) => void;
  releaseConfirmed: boolean;
  onReleaseConfirmedChange: (value: boolean) => void;
  busy: boolean;
}) {
  if (count === 0) return null;
  return <div className="portal-bulk-bar" role="toolbar" aria-label="Aktionen für ausgewählte Dokumente">
    <span>{count} ausgewählt</span>
    <label className="portal-choice portal-approval"><input type="checkbox" checked={releaseConfirmed} onChange={event => onReleaseConfirmedChange(event.target.checked)} />Ich habe die Inhalte geprüft und möchte die ausgewählten Dokumente freigeben.</label>
    {gradeCCount > 0 && <label className="portal-choice"><input type="checkbox" checked={acceptQualityWarnings} onChange={event => onAcceptQualityWarningsChange(event.target.checked)} />{gradeCCount} Dokument{gradeCCount === 1 ? '' : 'e'} mit Qualitätsstufe C trotzdem freigeben</label>}
    <Button variant="outline" size="sm" disabled={busy || !releaseConfirmed} onClick={onRelease}>Freigeben</Button>
    <Button variant="outline" size="sm" disabled={busy} onClick={onSkip}>Überspringen</Button>
    <Button variant="outline" size="sm" disabled={busy} onClick={onDelete}>Löschen</Button>
    <Button variant="ghost" size="sm" disabled={busy} onClick={onClear}>Auswahl aufheben</Button>
  </div>;
}
export function Pagination({ offset, total, onChange }: { offset: number; total: number; onChange: (offset: number) => void }) {
  if (total <= 20) return null;
  return <nav className="portal-pagination" aria-label="Dokumentseiten"><span>{offset + 1}–{Math.min(offset + 20, total)} von {total}</span><Button variant="outline" size="sm" disabled={!offset} onClick={() => onChange(Math.max(0, offset - 20))}>Zurück</Button><Button variant="outline" size="sm" disabled={offset + 20 >= total} onClick={() => onChange(offset + 20)}>Weiter</Button></nav>;
}

const qualityGradeChips: Array<{ value: '' | 'A' | 'B' | 'C' | 'none'; label: string }> = [
  { value: '', label: 'Alle' },
  { value: 'A', label: 'A' },
  { value: 'B', label: 'B' },
  { value: 'C', label: 'C' },
  { value: 'none', label: 'Ohne Bewertung' },
];

export function QualityGradeFilterRow({ value, onChange }: { value: '' | 'A' | 'B' | 'C' | 'none'; onChange: (value: '' | 'A' | 'B' | 'C' | 'none') => void }) {
  return <div className="portal-filter-row" role="group" aria-label="Nach Qualitätsstufe filtern">{qualityGradeChips.map(chip => <button key={chip.value || 'all'} type="button" aria-pressed={value === chip.value} onClick={() => onChange(chip.value)}>{chip.label}</button>)}</div>;
}

// Plain-German explanation of the automatic quality grades, reused next to
// the grade filter and inside a document's own "Warum Stufe X?" block.
export function QualityGradeLegend() {
  return <details className="portal-quality-legend"><summary>Was bedeuten die Qualitätsstufen?</summary>
    <dl>
      <dt>Stufe A</dt><dd>Sehr gut lesbar, direkt geeignet.</dd>
      <dt>Stufe B</dt><dd>Gut, aber mit Hinweisen – sorgfältig prüfen.</dd>
      <dt>Stufe C</dt><dd>Qualitätsmängel – bewusste Freigabe nach Prüfung nötig.</dd>
      <dt>Ohne Bewertung</dt><dd>Keine automatische Prüfung durchgeführt, z. B. bei Confluence-Importen oder noch nicht abgeschlossener Verarbeitung.</dd>
    </dl>
  </details>;
}
