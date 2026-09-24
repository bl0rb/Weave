'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { FileInput, FilePlus, Inbox, LoaderCircle, RefreshCcw, RotateCcw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ImportSyncButton } from '@/components/portal/import-sync';
import { ApiError, apiJson } from '@/lib/api';
import { formatBytes } from '@/components/dashboard/shared';
import { type ImportRun, type ImportRunListResponse, isRunActive, runStatusChip, runTitle } from '@/lib/imports';
import { useI18n } from '@/i18n/provider';

export default function ImportsPage() {
  const { t, locale } = useI18n();
  const [runs, setRuns] = useState<ImportRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const runsPayload = await apiJson<ImportRunListResponse>('/api/v1/import/runs', { cache: 'no-store' });
        if (cancelled) return;
        setRuns(runsPayload.items);
        setLoadError(null);
      } catch (error) {
        if (!cancelled) {
          setLoadError(error instanceof ApiError ? error.detail : t('portal.importsList.loadFailed'));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [reloadNonce, t]);

  const loadAll = () => {
    setLoading(true);
    setReloadNonce((nonce) => nonce + 1);
  };

  return (
    <main className="min-h-screen">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 text-slate-950 sm:px-6 lg:px-8">
        <section className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-3xl font-semibold">{t('portal.importsList.title')}</h1>
            <p className="mt-2 text-slate-600">
              {t('portal.importsList.subtitle')}
            </p>
          </div>
          <Button variant="outline" onClick={() => void loadAll()} disabled={loading}>
            <RefreshCcw className="mr-2 h-4 w-4" /> {t('common.refresh')}
          </Button>
        </section>

        <section className="mb-6 flex flex-wrap items-center justify-center gap-3">
          <Link href="/processing/new">
            <Button variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 hover:text-emerald-900">
              <FilePlus className="mr-2 h-4 w-4" /> {t('portal.importsList.newFileTask')}
            </Button>
          </Link>
          <Link href="/imports/new">
            <Button variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 hover:text-emerald-900">
              <FileInput className="mr-2 h-4 w-4" /> {t('portal.importsList.newImport')}
            </Button>
          </Link>
        </section>

        {loadError && (
          <p role="alert" className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {loadError}
          </p>
        )}

        <section className="mb-6 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-4">
            <h2 className="text-[17px] font-semibold">{t('portal.importsList.runsHeading')}</h2>
            <p className="text-sm text-slate-500">{t('portal.importsList.runsCount', { count: runs.length })}</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full table-auto text-left text-xs sm:text-sm">
              <thead className="text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">{t('portal.importsList.colImport')}</th>
                  <th className="pb-2 font-medium">{t('portal.importsList.colStatus')}</th>
                  <th className="pb-2 font-medium">{t('portal.importsList.colPages')}</th>
                  <th className="hidden pb-2 font-medium sm:table-cell">{t('portal.importsList.colAttachments')}</th>
                  <th className="hidden pb-2 font-medium md:table-cell">{t('portal.importsList.colSize')}</th>
                  <th className="hidden pb-2 font-medium md:table-cell">{t('portal.importsList.colCreated')}</th>
                  <th className="pb-2 font-medium" aria-label={t('portal.importsList.colActions')} />
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className="border-t border-slate-100">
                    <td className="py-3">
                      <Link href={`/imports/${run.id}`} className="line-clamp-2 font-medium text-slate-950 hover:text-emerald-700">
                        {runTitle(run, locale)}
                      </Link>
                      <p className="mt-1 text-xs text-slate-500">
                        {run.scope_type === 'space' ? t('portal.importsList.spaceKeyLabel', { value: run.scope_value }) : t('portal.importsList.pageIdLabel', { value: run.scope_value })}
                        {run.owner ? ` · ${run.owner.username}` : ''}
                      </p>
                    </td>
                    <td className="py-3">
                      <span className={`rounded px-2 py-1 text-xs ${runStatusChip[run.status]}`}>{run.status}</span>
                      {!!run.missing_page_count && <Link href={`/imports/${run.id}`} className="mt-2 block text-xs text-amber-800">{t('portal.importsList.missingPages', { count: run.missing_page_count })}</Link>}
                    </td>
                    <td className="py-3 text-slate-700">
                      {run.pages_imported} / {run.pages_discovered}
                      {run.pages_failed > 0 && <span className="ml-1 text-xs text-red-600">{t('portal.importsList.failedCount', { count: run.pages_failed })}</span>}
                    </td>
                    <td className="hidden py-3 text-slate-700 sm:table-cell">{run.attachments_saved}</td>
                    <td className="hidden py-3 text-slate-700 md:table-cell">
                      {formatBytes(run.artifact_bytes + run.content_bytes)}
                    </td>
                    <td className="hidden py-3 text-slate-700 md:table-cell">{new Date(run.created_at).toLocaleString()}</td>
                    <td className="py-3 text-right">
                      {run.can_sync && <ImportSyncButton runId={run.id} compact />}
                      {!isRunActive(run.status) && run.can_edit !== false && (
                        <Link
                          href={`/imports/new?from=${run.id}`}
                          title={t('portal.importsList.editRerun')}
                          aria-label={t('portal.importsList.editRerun')}
                          className="inline-flex items-center rounded p-1.5 text-slate-500 hover:bg-slate-100 hover:text-emerald-700"
                        >
                          <RotateCcw className="h-4 w-4" />
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {runs.length === 0 && !loading && (
              <div className="flex flex-col items-center gap-3 py-10 text-center">
                <Inbox className="h-8 w-8 text-slate-300" />
                <p className="text-sm text-slate-500">{t('portal.importsList.emptyBody')}</p>
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <Link href="/processing/new">
                    <Button variant="outline" size="sm" className="border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 hover:text-emerald-900">
                      <FilePlus className="h-4 w-4" />
                      {t('portal.importsList.newFileTask')}
                    </Button>
                  </Link>
                  <Link href="/imports/new">
                    <Button variant="outline" size="sm" className="border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 hover:text-emerald-900">
                      <FileInput className="h-4 w-4" />
                      {t('portal.importsList.newImport')}
                    </Button>
                  </Link>
                </div>
              </div>
            )}
            {loading && (
              <div className="flex items-center gap-2 py-6 text-sm text-slate-600">
                <LoaderCircle className="h-4 w-4 animate-spin" /> {t('portal.importsList.loading')}
              </div>
            )}
          </div>
        </section>

        <p className="text-sm text-slate-500">
          {t('portal.importsList.connectionsManagedPrefix')}{' '}
          <Link href="/connections?tab=confluence" className="text-emerald-700 hover:text-emerald-800">
            {t('portal.importsList.connectionsManagedLink')}
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
