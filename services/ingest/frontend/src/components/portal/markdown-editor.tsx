'use client';

import { useState } from 'react';
import { ApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { MarkdownView } from '@/components/markdown/markdown-view';
import { editPortalDocument, portalError, type DocumentPreview, type PortalEditResult } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { Notice } from './shared';

/**
 * Edit a document's Markdown and release the change at once (Besitzer and
 * Mitglieder, like a release). The change applies until a changed source --
 * a new upload or a Confluence sync -- is released.
 */
export function MarkdownEditor({ preview, onSaved, onCancel }: {
  preview: DocumentPreview; onSaved: (result: PortalEditResult) => void; onCancel: () => void;
}) {
  const { t, locale } = useI18n();
  const [draft, setDraft] = useState(preview.markdown);
  const [tab, setTab] = useState<'source' | 'preview'>('source');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // Set once the server reports that the edited text only reaches grade C.
  const [needsGradeC, setNeedsGradeC] = useState(false);
  const [gradeCAccepted, setGradeCAccepted] = useState(false);
  const unchanged = draft === preview.markdown;

  async function save() {
    if (saving || unchanged || (needsGradeC && !gradeCAccepted)) return;
    setSaving(true); setError('');
    try {
      onSaved(await editPortalDocument(preview.id, draft, preview.markdown_sha256, needsGradeC && gradeCAccepted));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && /grade C/i.test(err.detail)) {
        setNeedsGradeC(true);
      } else {
        setError(portalError(err, locale));
      }
    } finally { setSaving(false); }
  }

  return <div className="portal-markdown-editor">
    <div role="tablist" aria-label={t('portal.reviews.editHeading')} className="mb-3 inline-flex gap-1 rounded-lg border border-[var(--line)] bg-[var(--surface)] p-1">
      {(['source', 'preview'] as const).map(id => <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
        className={`h-8 rounded-md px-3 text-sm font-semibold ${tab === id ? 'bg-[var(--accent-soft)] text-[var(--accent-ink)]' : 'text-[var(--ink-2)] hover:bg-[var(--hover)]'}`}>
        {id === 'source' ? t('portal.reviews.editTabSource') : t('portal.reviews.editTabPreview')}
      </button>)}
    </div>
    {tab === 'source'
      ? <textarea aria-label={t('portal.reviews.editLabel')} value={draft} spellCheck={false} disabled={saving}
        onChange={event => { setDraft(event.target.value); setNeedsGradeC(false); setGradeCAccepted(false); }}
        className="!min-h-[480px] font-mono !text-[13px] leading-relaxed" />
      : <MarkdownView markdown={draft} jobId={preview.id} />}
    <p className="portal-field-hint">{t('portal.reviews.editHint')}</p>
    {error && <Notice error>{error}</Notice>}
    {needsGradeC && <label className="portal-choice portal-approval">
      <input type="checkbox" checked={gradeCAccepted} disabled={saving} onChange={event => setGradeCAccepted(event.target.checked)} />
      {t('portal.reviews.editConfirmGradeC')}
    </label>}
    <div className="portal-form-actions">
      <Button disabled={saving || unchanged || (needsGradeC && !gradeCAccepted)} onClick={() => void save()}>
        {saving ? t('portal.reviews.editSaving') : t('portal.reviews.editSave')}
      </Button>
      <Button variant="ghost" disabled={saving} onClick={onCancel}>{t('portal.reviews.editCancel')}</Button>
    </div>
  </div>;
}
