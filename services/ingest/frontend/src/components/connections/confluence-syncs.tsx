'use client';

import { useState } from 'react';
import Link from 'next/link';
import { LoaderCircle, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ConfirmDialog, Toggle, apiSend, errorMessage } from '@/components/admin/admin-shared';
import { apiJson } from '@/lib/api';
import {
  formatRefreshInterval,
  refreshIntervalOptions,
  runScopeLabel,
  runStatusChip,
  runTitle,
  type ImportRun,
  type ImportSourceKind,
  type ImportSync,
} from '@/lib/imports';
import { syncScopeLabel } from '@/components/portal/import-sync';
import { useI18n } from '@/i18n/provider';

/** The configured imports (syncs) of one Confluence connection: what is
 * imported where, its auto-sync schedule and state, and the actions to run,
 * edit or remove it. A connection can have any number of them. */
export function ConfluenceSyncs({
  sourceId,
  syncs,
  onChanged,
  kind = 'confluence',
}: {
  sourceId: string;
  syncs: ImportSync[];
  onChanged: () => Promise<void>;
  // Website connections: new sources start in the "add source" form, and
  // there is no Confluence wizard to edit them with.
  kind?: ImportSourceKind;
}) {
  const { t } = useI18n();
  const [removing, setRemoving] = useState<ImportSync | null>(null);
  const website = kind === 'website';

  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-950">{t('portal.connections.confluence.syncsHeading', { count: syncs.length })}</p>
        <Link
          href={website ? `/sources/new?kind=website&source=${encodeURIComponent(sourceId)}` : `/imports/new?source=${encodeURIComponent(sourceId)}`}
          className="inline-flex items-center gap-1 text-sm font-medium text-emerald-700 hover:text-emerald-800"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          {website ? t('portal.connections.website.syncAdd') : t('portal.connections.confluence.syncAdd')}
        </Link>
      </div>
      {syncs.length === 0 ? (
        <p className="text-xs text-slate-500">{website ? t('portal.connections.website.syncNone') : t('portal.connections.confluence.syncNone')}</p>
      ) : (
        <ul className="space-y-2">
          {syncs.map((sync) => (
            <SyncRow key={sync.id} sync={sync} website={website} onChanged={onChanged} onRemove={() => setRemoving(sync)} />
          ))}
        </ul>
      )}
      {removing && (
        <ConfirmDialog
          title={t('portal.connections.confluence.syncRemoveTitle')}
          body={<p>{t('portal.connections.confluence.syncRemoveBody', { title: runTitle(removing) })}</p>}
          confirmLabel={t('portal.connections.confluence.syncRemove')}
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await apiSend(`/api/v1/import/syncs/${removing.id}`, { method: 'DELETE' });
            setRemoving(null);
            await onChanged();
          }}
        />
      )}
    </div>
  );
}

function SyncRow({ sync, website, onChanged, onRemove }: { sync: ImportSync; website: boolean; onChanged: () => Promise<void>; onRemove: () => void }) {
  const { t, locale } = useI18n();
  const intervalOptions = refreshIntervalOptions(locale);
  const [busy, setBusy] = useState<'patch' | 'run' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [startedRunId, setStartedRunId] = useState<string | null>(null);
  const lastRun = sync.last_run;

  const patch = async (body: { enabled?: boolean; interval_seconds?: number }) => {
    setBusy('patch');
    setError(null);
    try {
      await apiJson<ImportSync>(`/api/v1/import/syncs/${sync.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      await onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const runNow = async () => {
    setBusy('run');
    setError(null);
    try {
      const run = await apiJson<ImportRun>(`/api/v1/import/syncs/${sync.id}/run`, { method: 'POST' });
      setStartedRunId(run.id);
      await onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <li className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-950">
            {runTitle(sync, locale)}
            {sync.root_page_title.trim() && <span className="font-normal text-slate-500"> · {runScopeLabel(sync, locale)}</span>}
          </p>
          {website && <p className="mt-0.5">{t('portal.importSync.scopeLabel')}: {syncScopeLabel(sync, t)}</p>}
          <p className="mt-0.5">
            {sync.collection_name
              ? t('portal.connections.confluence.syncTarget', { name: sync.collection_name })
              : t('portal.connections.confluence.syncNoTarget')}
          </p>
        </div>
        <div className="flex flex-shrink-0 items-center gap-1">
          <Button variant="outline" size="sm" onClick={() => void runNow()} disabled={busy !== null}>
            {busy === 'run' ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {t('portal.connections.confluence.syncNow')}
          </Button>
          {!website && <Link
            href={`/imports/new?sync=${encodeURIComponent(sync.id)}`}
            aria-label={t('portal.connections.confluence.syncEditAria', { title: runTitle(sync, locale) })}
            title={t('portal.connections.confluence.syncEdit')}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-white hover:text-slate-700"
          >
            <Pencil className="h-4 w-4" />
          </Link>}
          <button
            type="button"
            onClick={onRemove}
            aria-label={t('portal.connections.confluence.syncRemoveAria', { title: runTitle(sync, locale) })}
            title={t('portal.connections.confluence.syncRemove')}
            disabled={busy !== null}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-50 hover:text-red-600 disabled:pointer-events-none disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3">
        <Toggle
          checked={sync.enabled}
          onChange={(next) => void patch({ enabled: next })}
          label={t('portal.connections.confluence.autoRefresh')}
          disabled={busy !== null}
        />
        <select
          value={sync.interval_seconds ?? intervalOptions[2].value}
          onChange={(event) => void patch({ interval_seconds: Number(event.target.value) })}
          disabled={!sync.enabled || busy !== null}
          aria-label={t('portal.connections.confluence.syncIntervalAria')}
          className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-950 disabled:opacity-50"
        >
          {/* The server floor (e.g. 900 s) can be a value none of the presets cover. */}
          {sync.interval_seconds != null && !intervalOptions.some((option) => option.value === sync.interval_seconds) && (
            <option value={sync.interval_seconds}>{formatRefreshInterval(sync.interval_seconds, locale)}</option>
          )}
          {intervalOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <p className="mt-1.5 text-slate-500">
        {sync.last_success_at
          ? t('portal.connections.confluence.syncLastSuccess', { date: new Date(sync.last_success_at).toLocaleString() })
          : t('portal.connections.confluence.syncNeverSucceeded')}
        {sync.next_refresh_at && (
          <>
            {' · '}
            {new Date(sync.next_refresh_at) <= new Date()
              ? t('portal.connections.confluence.nextRefreshDue')
              : t('portal.connections.confluence.nextRefresh', { date: new Date(sync.next_refresh_at).toLocaleString() })}
          </>
        )}
      </p>
      {lastRun && (
        <p className="mt-1 flex flex-wrap items-center gap-1.5">
          <span>{t('portal.connections.confluence.lastRun')}:</span>
          <Link href={`/imports/${lastRun.id}`} className={`rounded px-1.5 py-0.5 no-underline ${runStatusChip[lastRun.status]}`}>
            {t(`portal.connections.confluence.runStatus.${lastRun.status}`)}
          </Link>
          <span className="text-slate-500">
            {new Date(lastRun.finished_at ?? lastRun.created_at).toLocaleString()} ·{' '}
            {lastRun.is_refresh ? t('portal.connections.confluence.lastRunRefresh') : t('portal.connections.confluence.lastRunManual')}
          </span>
        </p>
      )}
      {sync.last_error && <p className="mt-1 text-red-600">{sync.last_error}</p>}
      {startedRunId && (
        <p role="status" className="mt-1 text-emerald-800">
          {t('portal.connections.confluence.syncNowStarted')}{' '}
          <Link href={`/imports/${startedRunId}`} className="font-medium text-emerald-700 hover:text-emerald-800">
            {t('portal.connections.confluence.syncNowOpenRun')}
          </Link>
        </p>
      )}
      {error && <p role="alert" className="mt-1 text-red-600">{error}</p>}
    </li>
  );
}
