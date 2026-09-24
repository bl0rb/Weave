'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Ban, CheckCheck, Download, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { ApiError, apiJson } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { Button, buttonVariants } from '@/components/ui/button';
import { ConfirmDialog, apiSend } from '@/components/admin/admin-shared';
import { MarkdownView } from '@/components/markdown/markdown-view';
import { bulkPortalAction, dateLabel, documentState, downloadPortalFile, jsonBody, loadDocuments, markdownDownloadName, portalError, reindexPortalDocument, skipPortalDocument, unskipPortalDocument, type DocumentPage, type DocumentPreview, type PortalConfig, type QualityGradeFilter, type Publication, type ReviewStateFilter } from '@/lib/portal';
import { BulkActionBar, DocumentTable, EmptyState, Notice, Pagination, PortalPage, QualityGradeFilterRow, QualityGradeLegend } from './shared';
import { ReprocessForm } from './reprocess-form';
import { IndexingProgress } from './indexing-progress';
import { useIndexingStatus } from '@/lib/use-indexing-status';
import { currentReleaseStatus } from '@/lib/indexing-status';
import { useI18n } from '@/i18n/provider';
import { translate, type MessageKey } from '@/i18n/messages';
import type { Locale } from '@/i18n/config';

const qualityMissingReasonKeys: Record<string, MessageKey> = {
  not_finished: 'portal.reviews.quality.reason.notFinished',
  failed: 'portal.reviews.quality.reason.failed',
  legacy: 'portal.reviews.quality.reason.legacy',
  import_without_gate: 'portal.reviews.quality.reason.importWithoutGate',
  unknown: 'portal.reviews.quality.reason.unknown',
};
function qualityMissingReasonText(reason: string | null, locale: Locale): string {
  const key = (reason && qualityMissingReasonKeys[reason]) || qualityMissingReasonKeys.unknown;
  return translate(locale, key);
}

function reviewStateChips(t: (key: MessageKey) => string): Array<{ value: ReviewStateFilter; label: string }> {
  return [
    { value: 'review', label: t('portal.reviews.stateReview') },
    { value: 'all', label: t('portal.reviews.stateAll') },
    { value: 'skipped', label: t('portal.documents.state.skipped') },
  ];
}

export function ReviewInbox() {
  const { t, locale } = useI18n();
  const [documents, setDocuments] = useState<DocumentPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [reviewState, setReviewState] = useState<ReviewStateFilter>('review');
  const [qualityGrade, setQualityGrade] = useState<QualityGradeFilter>('');
  const [error, setError] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [acceptQualityWarnings, setAcceptQualityWarnings] = useState(false);
  const [releaseConfirmed, setReleaseConfirmed] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [notice, setNotice] = useState('');
  const load = useCallback(() => loadDocuments(undefined, offset, reviewState, qualityGrade || undefined)
    .then(docs => { setDocuments(docs); setError(''); })
    .catch(err => setError(portalError(err, locale))), [offset, reviewState, qualityGrade, locale]);
  useEffect(() => { void load(); }, [load]);
  const selectedDocuments = documents?.items.filter(document => selectedIds.has(document.id)) ?? [];
  const gradeCCount = selectedDocuments.filter(document => document.quality_grade?.toUpperCase() === 'C').length;
  function clearSelection() { setSelectedIds(new Set()); setAcceptQualityWarnings(false); setReleaseConfirmed(false); }
  async function runBulk(action: 'release' | 'skip' | 'delete') {
    if (bulkBusy || selectedIds.size === 0) return;
    setBulkBusy(true); setNotice('');
    try {
      const result = await bulkPortalAction([...selectedIds], action, acceptQualityWarnings);
      setNotice(`${t('portal.spaces.bulkDoneNotice', { count: result.done })}${result.errors.length ? t('portal.spaces.bulkErrorSuffix', { count: result.errors.length }) : ''}.`);
      clearSelection();
      setDocuments(null);
      await load();
    } catch (err) { setError(portalError(err, locale)); } finally { setBulkBusy(false); setBulkDeleting(false); }
  }
  return <PortalPage title={t('portal.reviews.title')} description={t('portal.reviews.description')} actions={<Button variant="outline" onClick={load}>{t('common.refresh')}</Button>}>
    <div className="portal-filter-row" role="group" aria-label={t('portal.reviews.selectionAria')}>{reviewStateChips(t).map(chip => <button key={chip.value} aria-pressed={chip.value === reviewState} onClick={() => { setReviewState(chip.value); setOffset(0); setDocuments(null); clearSelection(); }}>{chip.label}</button>)}</div>
    <QualityGradeFilterRow value={qualityGrade} onChange={value => { setQualityGrade(value); setOffset(0); setDocuments(null); }} />
    <QualityGradeLegend />
    {error && <Notice error action={load}>{error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    <BulkActionBar
      count={selectedIds.size}
      gradeCCount={gradeCCount}
      acceptQualityWarnings={acceptQualityWarnings}
      onAcceptQualityWarningsChange={setAcceptQualityWarnings}
      releaseConfirmed={releaseConfirmed}
      onReleaseConfirmedChange={setReleaseConfirmed}
      busy={bulkBusy}
      onRelease={() => void runBulk('release')}
      onSkip={() => void runBulk('skip')}
      onDelete={() => setBulkDeleting(true)}
      onClear={clearSelection}
    />
    <section className="portal-panel">{!documents && !error ? <p role="status" className="portal-loading">{t('portal.tasks.documentsLoading')}</p> : documents?.items.length ? <><DocumentTable documents={documents.items} selectedIds={selectedIds} onToggle={id => setSelectedIds(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; })} onToggleAll={checked => setSelectedIds(checked ? new Set(documents.items.map(document => document.id)) : new Set())} /><Pagination offset={offset} total={documents.total} onChange={value => { setDocuments(null); setOffset(value); }} /></> : !error && <EmptyState title={reviewState === 'review' ? t('portal.reviews.emptyReviewTitle') : reviewState === 'skipped' ? t('portal.reviews.emptySkippedTitle') : t('portal.reviews.emptyUnassignedTitle')} href="/sources/new" action={t('portal.chrome.addSource')}>{reviewState === 'review' ? t('portal.reviews.emptyReviewBody') : t('portal.reviews.emptyOtherBody')}</EmptyState>}</section>
    {bulkDeleting && <ConfirmDialog title={t('portal.spaces.deleteDocumentsTitle')} body={<p>{t('portal.spaces.deleteDocumentsBody', { count: selectedIds.size })}</p>} confirmLabel={t('portal.spaces.deleteDocumentsTitle')} onClose={() => setBulkDeleting(false)} onConfirm={() => runBulk('delete')} />}
  </PortalPage>;
}

export function ReviewDocument({ id }: { id: string }) {
  return <ReviewDocumentContent key={id} id={id} />;
}

function ReviewDocumentContent({ id }: { id: string }) {
  const router = useRouter();
  const { user } = useAuth();
  const { t, locale, formatNumber } = useI18n();
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [config, setConfig] = useState<PortalConfig | null>(null);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [reprocessStarted, setReprocessStarted] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloadingDiagnostics, setDownloadingDiagnostics] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [confirmReindex, setConfirmReindex] = useState(false);
  const startedHeading = useRef<HTMLHeadingElement>(null);
  const load = useCallback(() => Promise.all([apiJson<DocumentPreview>(`/api/v1/portal/documents/${encodeURIComponent(id)}`), apiJson<PortalConfig>('/api/v1/portal/config')])
    .then(([content, configuration]) => { setPreview(content); setConfig(configuration); setError(''); setConfirmed(false); setReprocessOpen(false); })
    .catch(err => { setPreview(null); setError(portalError(err, locale)); }), [id, locale]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (reprocessStarted) startedHeading.current?.focus(); }, [reprocessStarted]);
  async function release() {
    if (saving || reprocessOpen || reprocessStarted || !preview || !confirmed || !preview.can_release || !config?.publication_configured) return;
    setSaving(true); setError('');
    try {
      const publication = await apiJson<Publication>(`/api/v1/portal/documents/${encodeURIComponent(id)}/release`, jsonBody({ markdown_sha256: preview.markdown_sha256, ...(preview.quality_grade?.toUpperCase() === 'C' ? { accept_quality_warning: true } : {}) }));
      setPreview({ ...preview, release: publication }); setConfirmed(false);
    } catch (err) { setConfirmed(false); setError(portalError(err, locale)); } finally { setSaving(false); }
  }
  async function retry() {
    if (saving || !preview?.release) return;
    setSaving(true); setError('');
    try { const publication = await apiJson<Publication>(`/api/v1/portal/releases/${preview.release.id}/retry`, { method: 'POST' }); setPreview({ ...preview, release: publication }); }
    catch (err) { setError(portalError(err, locale)); } finally { setSaving(false); }
  }
  async function reindex() {
    if (saving || !preview?.release) return;
    setSaving(true); setError('');
    try {
      const publication = await reindexPortalDocument(id);
      setPreview({ ...preview, release: publication });
      setConfirmReindex(false);
    } catch (err) { setError(portalError(err, locale)); } finally { setSaving(false); }
  }
  async function skip() {
    if (skipping || !preview) return;
    setSkipping(true); setError('');
    try { const updated = await skipPortalDocument(id); setPreview({ ...preview, review_decision: updated.review_decision }); }
    catch (err) { setError(portalError(err, locale)); } finally { setSkipping(false); }
  }
  async function unskip() {
    if (skipping || !preview) return;
    setSkipping(true); setError('');
    try { const updated = await unskipPortalDocument(id); setPreview({ ...preview, review_decision: updated.review_decision }); }
    catch (err) { setError(portalError(err, locale)); } finally { setSkipping(false); }
  }
  async function downloadDiagnostics() {
    if (downloadingDiagnostics || !preview?.release || user?.role !== 'admin') return;
    setDownloadingDiagnostics(true); setError('');
    try {
      const filename = `${markdownDownloadName(preview.original_filename).slice(0, -3)}-indexing-diagnostics.json`;
      await downloadPortalFile(`/api/v1/portal/documents/${encodeURIComponent(id)}/indexing-diagnostics`, filename, locale);
    } catch (err) { setError(portalError(err, locale)); } finally { setDownloadingDiagnostics(false); }
  }
  async function reprocess(profileId: string) {
    if (saving || reprocessStarted || !preview?.can_reprocess || preview.release) return;
    setSaving(true); setConfirmed(false); setError('');
    try {
      await apiJson(`/api/v1/portal/documents/${encodeURIComponent(id)}/reprocess`, jsonBody({
        profile_id: profileId, markdown_sha256: preview.markdown_sha256,
      }));
      // The previous preview must disappear as soon as its replacement is queued.
      setReprocessStarted(true); setReprocessOpen(false);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409
        ? t('portal.reviews.reprocessConflict')
        : portalError(err, locale));
    } finally { setSaving(false); }
  }
  const { items: indexingItems } = useIndexingStatus(preview?.release ? [id] : []);
  const live = indexingItems[id];
  const state = preview && documentState(preview, live, locale);
  const releaseStatus = preview?.release ? currentReleaseStatus(preview.release, live) : null;
  const delivery = releaseStatus?.delivery ?? null;
  const indexingFailed = releaseStatus?.indexing?.state === 'failed';
  return <PortalPage title={preview?.original_filename || t('portal.reviews.detailTitleFallback')} description={t('portal.reviews.detailDescription')} eyebrow={t('portal.reviews.detailEyebrow')} actions={!reprocessStarted && <Button variant="outline" disabled={saving} onClick={load}>{t('portal.reviews.reloadAction')}</Button>}>
    <Link className="portal-back" href="/reviews">{t('portal.reviews.backLink')}</Link>
    {error && <Notice error action={load}>{error}</Notice>}
    {!preview && !error && <Notice>{t('portal.reviews.loadingDetail')}</Notice>}
    {reprocessStarted && <section className="portal-panel portal-form-panel" aria-labelledby="reprocess-started-title">
      <RefreshCw size={26} aria-hidden="true" className="mb-4" />
      <h2 id="reprocess-started-title" tabIndex={-1} ref={startedHeading}>{t('portal.reviews.reprocessStartedHeading')}</h2>
      <p className="mt-4">{t('portal.reviews.reprocessStartedBody1')}</p>
      <p className="mt-3">{t('portal.reviews.reprocessStartedBody2')}</p>
      <div className="portal-form-actions"><Link href="/processing" className={buttonVariants()}>{t('portal.reviews.viewProcessing')}</Link><Link href="/reviews" className={buttonVariants({ variant: 'ghost' })}>{t('portal.reviews.backToReview')}</Link></div>
    </section>}
    {preview && !reprocessStarted && <><div className="portal-context-bar"><Link href={`/knowledge/${preview.collection_id}`}>{preview.collection_name}</Link><span aria-live="polite" className={`portal-badge portal-badge-${state?.tone}`}>{state?.label}</span></div>
      <div className="portal-review-grid"><article className="portal-panel portal-preview"><h2>{preview.release ? t('portal.reviews.releasedHeading') : t('portal.reviews.processedHeading')}</h2><p className="portal-field-hint">{t('portal.reviews.previewHint')}</p><MarkdownView markdown={preview.markdown} jobId={preview.id} /></article>
      <aside className="portal-panel portal-release-panel"><ShieldCheck size={27} /><h2>{reprocessOpen ? t('portal.reviews.reprocessHeading') : t('portal.reviews.releaseHeading')}</h2>
        <dl><dt>{t('portal.reviews.qualityLabel')}</dt><dd>{preview.quality_grade ? t('portal.documents.grade', { grade: preview.quality_grade }) : t('portal.reviews.noAutoRating')}</dd><dt>{t('portal.reviews.originLabel')}</dt><dd>{preview.source?.url ? <a href={preview.source.url} target="_blank" rel="noopener noreferrer">{preview.source.label}</a> : preview.source?.path ? `${preview.source.label}: ${preview.source.path}` : preview.source?.label}</dd><dt>{t('portal.documents.columnAdded')}</dt><dd>{dateLabel(preview.created_at, locale)}</dd></dl>
        <details className="portal-quality-reason"><summary>{preview.quality_grade ? t('portal.reviews.whyGrade', { grade: preview.quality_grade }) : t('portal.reviews.whyNoGrade')}</summary>
          {preview.quality ? <>
            <ul>
              <li>{t('portal.reviews.quality.ocrConfidenceLabel')}{preview.quality.signals.ocr_confidence != null ? t('portal.reviews.quality.measured', { pct: Math.round(preview.quality.signals.ocr_confidence * 100), sample: formatNumber(preview.quality.signals.confidence_sample_size) }) : t('portal.reviews.quality.unmeasured')}</li>
              <li>{t('portal.reviews.quality.structureQuality', { pct: Math.round(preview.quality.signals.structure_quality * 100) })}</li>
              <li>{t('portal.reviews.quality.textQuality', { pct: Math.round(preview.quality.signals.text_quality * 100), noise: Math.round(preview.quality.signals.noise_penalty * 100) })}</li>
              <li>{t('portal.reviews.quality.fieldCheck', { count: preview.quality.issues.length })}{preview.quality.issues.length > 0 ? `: ${preview.quality.issues.join(', ')}` : ''}</li>
            </ul>
            <p>{t('portal.reviews.quality.thresholds', { a: Math.round(preview.quality.thresholds.A * 100), b: Math.round(preview.quality.thresholds.B * 100) })}</p>
            <p>{t('portal.reviews.quality.overallScoreLabel')}{preview.quality.score != null ? t('portal.reviews.quality.overallScoreValue', { pct: Math.round(preview.quality.score * 100) }) : t('portal.reviews.quality.unknown')}</p>
          </> : <p>{t('portal.reviews.quality.noAutoRatingReason', { reason: qualityMissingReasonText(preview.quality_missing_reason, locale) })}</p>}
          <QualityGradeLegend />
        </details>
        {preview.quality_grade?.trim().toUpperCase() === 'C' ? <Notice>{t('portal.reviews.noticeGradeC')}</Notice> : preview.quality_recommendation?.trim().toLowerCase() === 'block' && <Notice error>{t('portal.reviews.noticeBlocked')}</Notice>}
        {(!preview.quality_recommendation || preview.quality_recommendation.trim().toLowerCase() === 'warn') && <Notice>{t('portal.reviews.noticeWarn')}</Notice>}
        {preview.release ? <><div className="portal-release-receipt"><CheckCheck size={23} /><strong>{t('portal.reviews.releaseSaved')}</strong><span>{dateLabel(preview.release.created_at, locale)}</span>{preview.release.released_by && <span>{t('portal.documents.releasedBy', { name: preview.release.released_by })}</span>}</div><p>{t('portal.reviews.releaseImmutable')}</p>{delivery === 'failed' && <><Notice error>{t('portal.reviews.deliveryFailedNotice')}</Notice>{preview.can_release && <Button disabled={saving} onClick={retry}>{t('portal.reviews.retryDelivery')}</Button>}</>}<IndexingProgress release={preview.release} live={live} />{preview.can_release && (confirmReindex ? <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={saving} onClick={() => setConfirmReindex(false)}>{t('common.cancel')}</Button><Button variant={indexingFailed ? 'default' : 'outline'} disabled={saving} onClick={() => void reindex()}><RefreshCw size={16} />{saving ? t('portal.reviews.reindexTriggering') : t('portal.reviews.reindexConfirmYes')}</Button></div> : <Button className="w-full" variant={indexingFailed ? 'default' : 'outline'} disabled={saving} onClick={() => setConfirmReindex(true)}><RefreshCw size={16} />{t('portal.spaces.reindexConfirm')}</Button>)}{user?.role === 'admin' && <Button className="w-full whitespace-normal h-auto py-3" variant="outline" disabled={downloadingDiagnostics} onClick={() => void downloadDiagnostics()}><Download size={16} />{downloadingDiagnostics ? t('portal.reviews.diagnosticsDownloading') : t('portal.reviews.diagnosticsDownload')}</Button>}</> : preview.review_decision === 'skipped' ? <>
          <p className="portal-field-hint">{t('portal.reviews.skippedHint')}</p>
          <Button className="w-full" variant="outline" disabled={skipping} onClick={() => void unskip()}>{skipping ? t('portal.reviews.unskipping') : t('portal.reviews.unskip')}</Button>
        </> : reprocessOpen ? <ReprocessForm currentProfileId={preview.profile_id} busy={saving} onSubmit={reprocess} onCancel={() => { setReprocessOpen(false); setError(''); }} /> : <>
          {preview.can_reprocess && <Button className="w-full whitespace-normal h-auto py-3" variant="outline" disabled={saving} onClick={() => { setReprocessOpen(true); setConfirmed(false); setError(''); }}>{t('portal.reviews.reprocessTrigger')}</Button>}
          {!preview.release && preview.can_release && <Button className="w-full" variant="outline" disabled={skipping} onClick={() => void skip()}><Ban size={16} />{skipping ? t('portal.reviews.skipping') : t('portal.reviews.skipAction')}</Button>}
          {!deleting && <Button className="w-full" variant="outline" disabled={saving} onClick={() => setDeleting(true)}><Trash2 size={16} />{t('portal.reviews.deleteDocument')}</Button>}
          {!config?.publication_configured && <Notice>{t('portal.reviews.publicationNotConfigured')}</Notice>}
          {!preview.can_release && preview.quality_recommendation !== 'block' && <p className="portal-field-hint">{t('portal.reviews.releasePermissionHint')}</p>}
          <label className="portal-choice portal-approval"><input type="checkbox" checked={confirmed} disabled={saving || !preview.can_release || !config?.publication_configured} onChange={event => setConfirmed(event.target.checked)} />{preview.quality_grade?.toUpperCase() === 'C' ? t('portal.reviews.confirmGradeC') : t('portal.reviews.confirmDefault')}</label>
          <Button className="w-full" disabled={saving || !confirmed || !preview.can_release || !config?.publication_configured} onClick={release}>{saving ? t('portal.reviews.releasing') : t('portal.reviews.releaseAction')}</Button>
          <Link href="/reviews" className="portal-defer">{t('portal.reviews.reviewLater')}</Link>
        </>}
      </aside></div></>}
    {deleting && preview && <ConfirmDialog title={t('portal.reviews.deleteDocument')} body={<p>{t('portal.reviews.deleteDialogBodyPrefix')} <strong className="text-slate-950">{preview.original_filename}</strong> {t('portal.reviews.deleteDialogBodySuffix')}</p>} confirmLabel={t('portal.reviews.deleteDocument')} onClose={() => setDeleting(false)} onConfirm={async () => { await apiSend(`/api/v1/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }); router.push('/reviews'); router.refresh(); }} />}
  </PortalPage>;
}
