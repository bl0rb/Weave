import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { collectionDownloadName, documentState, markdownDownloadName, portalDownloadError, portalError, type PortalDocument } from './portal';

const document: PortalDocument = { id: 'a', original_filename: 'Wissen.pdf', status: 'FINISHED', collection_id: 'c', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null };
describe('publication state', () => {
  it('never labels processed content as published', () => { expect(documentState(document).label).toBe('Bereit zur Prüfung'); });
  it('distinguishes delivery acknowledgement from completed indexing', () => {
    const sent = { ...document, release: { id: 'r', status: 'sent' as const, created_at: document.created_at, error_message: null } };
    expect(documentState(sent).label).toBe('Zur Indexierung übergeben');
    expect(documentState(sent).label).not.toMatch(/aktiv|verfügbar/i);
  });
  it('keeps quality blocks visible', () => { expect(documentState({ ...document, quality_recommendation: 'block' }).tone).toBe('error'); });
  it('does not expose internal errors or tokens', () => { expect(portalError(new ApiError(500, 'secret-key=example SQL error'))).not.toContain('secret'); });
});

describe('portal download names', () => {
  it('creates flat Markdown filenames from client supplied paths', () => {
    expect(markdownDownloadName('../Vertrag: Süd?.PDF')).toBe('Vertrag_ Süd_.md');
    expect(markdownDownloadName('ohne-endung')).toBe('ohne-endung.md');
  });

  it('keeps dots in knowledge area names when naming the ZIP', () => {
    expect(collectionDownloadName({ name: 'HR.Wissen 2026', slug: 'hr-wissen' })).toBe('HR.Wissen 2026-markdown.zip');
  });

  it('explains an empty export without exposing a server detail', () => {
    expect(portalDownloadError(new ApiError(404, 'internal marker'))).toMatch(/noch keine fertige Markdown-Datei/);
    expect(portalDownloadError(new ApiError(404, 'internal marker'))).not.toContain('internal');
  });
});
