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
export function DocumentTable({ documents, onDownloadMarkdown, downloadingId }: {
  documents: PortalDocument[];
  onDownloadMarkdown?: (document: PortalDocument) => void;
  downloadingId?: string | null;
}) {
  const { items } = useIndexingStatus(documents.filter(document => document.release).map(document => document.id));
  return <div className="portal-table-scroll"><table className="portal-table"><caption className="sr-only">Dokumente und ihr Verarbeitungs- und Freigabestatus</caption><thead><tr><th scope="col">Dokument</th><th scope="col">Wissensbereich</th><th scope="col">Stand</th><th scope="col">Hinzugefügt</th><th scope="col"><span className="sr-only">Öffnen</span></th></tr></thead><tbody>{documents.map(document => {
    const state = documentState(document, items[document.id]);
    return <tr key={document.id}><td><Link className="portal-document-link" href={documentUrl(document)}><FileText size={17} aria-hidden="true" /><span>{document.original_filename}</span></Link></td><td><Link href={`/knowledge/${document.collection_id}`}>{document.collection_name || 'Wissensbereich'}</Link></td><td><div aria-live="polite"><span className={`portal-badge portal-badge-${state.tone}`}>{state.label}</span>{state.hint && <span className="mt-2 block max-w-[300px] text-xs leading-5 text-slate-500">{state.hint}</span>}</div></td><td className="portal-date">{dateLabel(document.created_at)}</td><td><div className="portal-row-actions">{onDownloadMarkdown && document.status === 'FINISHED' && <button className="portal-open" type="button" disabled={downloadingId === document.id} onClick={() => onDownloadMarkdown(document)} aria-label={`${document.original_filename} als Markdown herunterladen`} title="Markdown herunterladen"><Download size={17} aria-hidden="true" /></button>}<Link className="portal-open" href={documentUrl(document)} aria-label={`${document.original_filename} öffnen`}><ArrowRight size={17} /></Link></div></td></tr>;
  })}</tbody></table></div>;
}
export function Pagination({ offset, total, onChange }: { offset: number; total: number; onChange: (offset: number) => void }) {
  if (total <= 20) return null;
  return <nav className="portal-pagination" aria-label="Dokumentseiten"><span>{offset + 1}–{Math.min(offset + 20, total)} von {total}</span><Button variant="outline" size="sm" disabled={!offset} onClick={() => onChange(Math.max(0, offset - 20))}>Zurück</Button><Button variant="outline" size="sm" disabled={offset + 20 >= total} onClick={() => onChange(offset + 20)}>Weiter</Button></nav>;
}
