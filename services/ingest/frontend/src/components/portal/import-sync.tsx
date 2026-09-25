'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { LoaderCircle, RefreshCw, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { type ImportRun, type MissingConfluencePage } from '@/lib/imports';
import { useI18n } from '@/i18n/provider';

export function ImportSyncButton({ runId, compact = false }: { runId: string; compact?: boolean }) {
  const { t } = useI18n();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function sync() {
    setBusy(true); setError('');
    try {
      const run = await apiJson<ImportRun>(`/api/v1/import/runs/${encodeURIComponent(runId)}/sync`, { method: 'POST' });
      router.push(`/imports/${run.id}`);
    } catch (error) { setError(error instanceof Error ? error.message : t('portal.importSync.syncFailed')); setBusy(false); }
  }
  return <div className="inline-flex max-w-full flex-col items-start gap-2">
    <Button type="button" size="sm" variant="outline" title={t('portal.importSync.syncNow')} aria-label={t('portal.importSync.syncNow')} disabled={busy} onClick={sync}>
      {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
      {!compact && t('portal.importSync.syncNow')}
    </Button>
    {error && <p role="alert" className="max-w-xs break-words text-sm text-red-700">{error}</p>}
  </div>;
}

export function MissingConfluencePages({ runId, pages, canRemove, onChanged }: {
  runId: string; pages: MissingConfluencePage[]; canRemove: boolean; onChanged: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  async function remove(page: MissingConfluencePage) {
    if (!window.confirm(t('portal.importSync.removeConfirm', { title: page.title || page.page_id }))) return;
    setBusy(page.page_id); setError('');
    try {
      await apiJson(`/api/v1/import/runs/${encodeURIComponent(runId)}/missing/${encodeURIComponent(page.page_id)}/withdraw`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true }),
      });
      await onChanged();
    } catch (error) { setError(error instanceof Error ? error.message : t('portal.importSync.removeFailed')); }
    finally { setBusy(null); }
  }
  if (!pages.length) return null;
  return <section className="mb-6 border-y border-amber-200 py-5" aria-labelledby="missing-confluence-title">
    <h2 id="missing-confluence-title" className="text-[17px] font-semibold">{t('portal.importSync.missingHeading')}</h2>
    <p className="mt-2 text-sm text-slate-700">{t('portal.importSync.missingBody')}</p>
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    <ul className="mt-4 divide-y divide-slate-200">{pages.map(page => <li key={page.page_id} className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0 flex-1 break-words"><strong>{page.title || page.page_id}</strong><p className="text-xs text-slate-500">{t('portal.importSync.pageIdLabel', { id: page.page_id })}</p>
        {page.withdrawal_error && <p className="mt-1 text-sm text-amber-800">{t('portal.importSync.withdrawalFailed')}</p>}
      </div>
      {page.withdrawal_status ? <span role="status" className="text-sm">{page.withdrawal_status === 'sent' ? t('portal.importSync.removed') : t('portal.importSync.removalPending')}</span> : canRemove && <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => remove(page)}>
        {busy === page.page_id ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}{t('portal.importSync.removeAction')}
      </Button>}
    </li>)}</ul>
  </section>;
}