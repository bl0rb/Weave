import { ApiError, apiFetch, apiJson } from '@/lib/api';
import { currentReleaseStatus, publicationState, type IndexingItem } from './indexing-status';

export type KnowledgeSpace = { collection_id: string; slug: string; name: string; description: string | null; read_teams: string[]; can_manage: boolean };
export type Publication = { id: string; created_at: string; status: 'pending' | 'sent' | 'failed'; error_message: string | null };
export type PortalDocument = {
  id: string; original_filename: string; status: 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
  collection_id: string; collection_name: string; created_at: string;
  quality_grade: string | null; quality_recommendation: string | null; can_release: boolean; release: Publication | null;
};
export type DocumentPage = { items: PortalDocument[]; total: number };
export type DocumentPreview = PortalDocument & { markdown: string; markdown_sha256: string; profile_id: string | null; can_reprocess: boolean };
export type PortalConfig = { publication_configured: boolean; team_name: string | null };
export const jsonBody = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function portalError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Bitte melde dich erneut an.';
    if (error.status === 403) return 'Für diese Aktion fehlen dir die Berechtigungen.';
    if (error.status === 404) return 'Dieser Inhalt ist nicht verfügbar oder wurde noch nicht für dich freigegeben.';
    if (error.status === 409) return 'Der Stand hat sich geändert oder kann noch nicht freigegeben werden. Bitte lade ihn erneut und prüfe die Hinweise.';
    if (error.status === 413) return 'Die Datei ist zu groß. Bitte wähle eine kleinere Datei.';
    if (error.status === 422) return 'Bitte prüfe deine Eingaben. Ein Wert ist ungültig oder fehlt.';
    if (error.status === 429) return 'Zu viele Anfragen. Bitte versuche es gleich noch einmal.';
    if (error.status === 503) return 'Der Dienst ist noch nicht bereit. Bitte versuche es später erneut oder wende dich an die Administration.';
  }
  return 'Die Anfrage konnte nicht abgeschlossen werden. Bitte prüfe die Verbindung und versuche es erneut.';
}

export function portalDownloadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return 'In diesem Wissensbereich ist noch keine fertige Markdown-Datei verfügbar.';
  }
  return portalError(error);
}

export function documentState(document: PortalDocument, live?: IndexingItem): { label: string; tone: 'neutral' | 'working' | 'warning' | 'success' | 'error'; hint?: string } {
  if (document.release) {
    const { delivery, indexing } = currentReleaseStatus(document.release, live);
    return publicationState(delivery, indexing);
  }
  if (document.status === 'FAILED') return { label: 'Verarbeitung fehlgeschlagen', tone: 'error' };
  if (document.status === 'RUNNING') return { label: 'Wird verarbeitet', tone: 'working' };
  if (document.status === 'PENDING') return { label: 'In der Warteschlange', tone: 'neutral' };
  if (document.quality_recommendation === 'block') return { label: 'Qualitätsprüfung blockiert', tone: 'error' };
  return { label: 'Bereit zur Prüfung', tone: 'warning' };
}
export const documentUrl = (document: PortalDocument) => document.status === 'FINISHED' ? `/reviews/${document.id}` : `/jobs/${document.id}`;
export const dateLabel = (value: string) => new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(value));
export function loadDocuments(collectionId?: string, offset = 0, reviewOnly = false): Promise<DocumentPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: '20' });
  if (collectionId) params.set('collection_id', collectionId);
  if (reviewOnly) params.set('review_only', 'true');
  return apiJson(`/api/v1/portal/documents?${params}`);
}

export function markdownDownloadName(originalFilename: string): string {
  const basename = originalFilename.replaceAll('\\', '/').split('/').at(-1) || 'dokument';
  const dot = basename.lastIndexOf('.');
  const stem = (dot > 0 ? basename.slice(0, dot) : basename)
    .replace(/[^\p{L}\p{N} .()_-]+/gu, '_')
    .replace(/^[ .]+|[ .]+$/g, '') || 'dokument';
  return `${stem}.md`;
}

export function collectionDownloadName(space: Pick<KnowledgeSpace, 'name' | 'slug'>): string {
  return `${markdownDownloadName(`${space.name || space.slug}.md`).slice(0, -3)}-markdown.zip`;
}

export async function downloadPortalFile(path: string, filename: string): Promise<void> {
  const response = await apiFetch(path);
  if (!response.ok) {
    let detail = `Download fehlgeschlagen (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // Keep the stable user-facing fallback for non-JSON error bodies.
    }
    throw new ApiError(response.status, detail);
  }
  const blob = await response.blob();
  const href = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(href);
}
