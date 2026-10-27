'use client';

import { useCallback, useEffect, useState } from 'react';
import { Cable, CircleCheck, CircleX, LoaderCircle, Pencil, PlugZap, Plus, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { Badge, ConfirmDialog, ErrorNotice, errorMessage, inputClass, LoadingState, SectionCard, apiSend } from '@/components/admin/admin-shared';
import { sourceKind, type ImportSource, type ImportSourceListResponse, type ImportSourceTestResponse, type ImportSync, type ImportSyncListResponse } from '@/lib/imports';
import { useImportSourceTest } from '@/lib/use-import-source-test';
import { useI18n } from '@/i18n/provider';
import { WebsiteSetupDialog } from './website-setup-dialog';
import { ConfluenceSyncs } from './confluence-syncs';

/** Verbindungen › Websites: the signed-in user's website / intranet
 * connections (credentials live here, write-only) and the sources set up
 * through each of them. Which pages go where is decided in "add source". */
export function WebsiteConnectionsTab() {
  const { t } = useI18n();
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [syncs, setSyncs] = useState<ImportSync[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<ImportSource | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ImportSourceTestResponse>>({});
  const { test, testingId, cooldownSecondsFor } = useImportSourceTest();

  const reload = useCallback(async () => {
    try {
      const [data, syncData] = await Promise.all([
        apiJson<ImportSourceListResponse>('/api/v1/import/sources', { cache: 'no-store' }),
        apiJson<ImportSyncListResponse>('/api/v1/import/syncs', { cache: 'no-store' }),
      ]);
      setSources(data.items.filter(source => sourceKind(source) === 'website'));
      setSyncs(syncData.items);
      setListError(null);
    } catch (err) {
      setListError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);
  // Mount fetch kept inline (not `reload()`): react-hooks/set-state-in-effect
  // wants an effect's own body await-first, same as the Confluence tab.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [data, syncData] = await Promise.all([
          apiJson<ImportSourceListResponse>('/api/v1/import/sources', { cache: 'no-store' }),
          apiJson<ImportSyncListResponse>('/api/v1/import/syncs', { cache: 'no-store' }),
        ]);
        if (cancelled) return;
        setSources(data.items.filter(source => sourceKind(source) === 'website'));
        setSyncs(syncData.items);
        setListError(null);
      } catch (err) {
        if (!cancelled) setListError(errorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const testSource = async (source: ImportSource) => {
    setTestResults(prev => { const next = { ...prev }; delete next[source.id]; return next; });
    const outcome = await test(source.id);
    if (outcome.kind === 'result') {
      setTestResults(prev => ({ ...prev, [source.id]: outcome.result }));
      if (outcome.result.ok) setSources(current => current.map(entry => entry.id === source.id ? { ...entry, server_kind: outcome.result.server_kind ?? entry.server_kind, last_validated_at: new Date().toISOString() } : entry));
    } else {
      setTestResults(prev => ({ ...prev, [source.id]: { ok: false, detail: errorMessage(outcome.error), server_kind: null } }));
    }
  };

  const saveRename = async (source: ImportSource) => {
    const name = renameValue.trim();
    if (!name) { setActionError(t('portal.connections.confluence.nameEmpty')); return; }
    setBusyId(source.id);
    try {
      const updated = await apiJson<ImportSource>(`/api/v1/import/sources/${source.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
      setSources(current => current.map(entry => (entry.id === source.id ? updated : entry)));
      setRenamingId(null);
      setActionError(null);
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const authBadge = (source: ImportSource) => source.auth_type === 'http_basic' ? t('portal.connections.website.authBasicBadge')
    : source.auth_type === 'bearer' ? t('portal.connections.website.authBearerBadge') : t('portal.connections.website.authNoneBadge');

  return (
    <div className="space-y-6">
      <SectionCard title={t('portal.connections.website.title')} description={t('portal.connections.website.description')}
        actions={<Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" />{t('portal.connections.website.add')}</Button>}>
        <ErrorNotice message={listError} />
        {actionError && <ErrorNotice message={actionError} />}
        {loading ? <LoadingState label={t('portal.connections.confluence.loading')} /> : sources.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <Cable className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-500">{t('portal.connections.website.empty')}</p>
            <Button variant="outline" size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" />{t('portal.connections.website.add')}</Button>
          </div>
        ) : (
          <ul className="space-y-4">
            {sources.map(source => (
              <li key={source.id} className="rounded-xl border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    {renamingId === source.id ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <input value={renameValue} onChange={event => setRenameValue(event.target.value)} aria-label={t('portal.connections.confluence.nameAria')}
                          onKeyDown={event => { if (event.key === 'Enter') void saveRename(source); if (event.key === 'Escape') setRenamingId(null); }} className={inputClass} autoFocus />
                        <Button size="sm" onClick={() => void saveRename(source)} disabled={busyId === source.id}>{t('portal.connections.confluence.save')}</Button>
                        <Button size="sm" variant="outline" onClick={() => setRenamingId(null)}>{t('portal.connections.confluence.cancel')}</Button>
                      </div>
                    ) : (
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-slate-950">{source.name}</span>
                        <span className={`rounded px-2 py-0.5 text-xs ${source.server_kind ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
                          {source.server_kind ? t('portal.connections.website.badgeReachable') : t('portal.connections.confluence.badgeUntested')}
                        </span>
                        <Badge tone="slate">{authBadge(source)}</Badge>
                        {source.allow_private_target && <Badge tone="amber">{t('portal.connections.website.badgePrivate')}</Badge>}
                      </div>
                    )}
                    <dl className="mt-2 space-y-1 text-xs text-slate-500">
                      <div className="flex gap-2"><dt className="w-16 flex-shrink-0 font-medium">{t('portal.connections.confluence.baseUrlLabel')}</dt><dd className="break-all">{source.base_url}</dd></div>
                      <div className="flex gap-2"><dt className="w-16 flex-shrink-0 font-medium">{t('portal.connections.confluence.createdLabel')}</dt><dd>{new Date(source.created_at).toLocaleDateString()}</dd></div>
                    </dl>
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-1">
                    <Button variant="outline" size="sm" onClick={() => void testSource(source)} disabled={testingId !== null || cooldownSecondsFor(source.id) > 0}>
                      {testingId === source.id ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}
                      {cooldownSecondsFor(source.id) > 0 ? t('portal.connections.confluence.testWithCooldown', { seconds: cooldownSecondsFor(source.id) }) : t('portal.connections.confluence.test')}
                    </Button>
                    <button onClick={() => { setRenamingId(source.id); setRenameValue(source.name); setActionError(null); }} aria-label={t('portal.connections.confluence.renameAria', { name: source.name })} title={t('portal.connections.confluence.rename')} disabled={busyId === source.id}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-slate-700 disabled:pointer-events-none disabled:opacity-50"><Pencil className="h-4 w-4" /></button>
                    <button onClick={() => setDeleting(source)} aria-label={t('portal.connections.confluence.deleteAria', { name: source.name })} title={t('portal.connections.confluence.delete')} disabled={busyId === source.id}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-50 hover:text-red-600 disabled:pointer-events-none disabled:opacity-50"><Trash2 className="h-4 w-4" /></button>
                  </div>
                </div>
                {testResults[source.id] && (
                  <div className={`mt-3 rounded-xl border px-4 py-3 text-sm ${testResults[source.id].ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-red-200 bg-red-50 text-red-700'}`}>
                    <div className="flex items-center gap-2 font-medium">
                      {testResults[source.id].ok ? <CircleCheck className="h-4 w-4 flex-shrink-0" /> : <CircleX className="h-4 w-4 flex-shrink-0" />}
                      {testResults[source.id].ok ? t('portal.connections.confluence.testSuccess') : t('portal.connections.confluence.testFailed')}
                    </div>
                    {testResults[source.id].detail && <p className="mt-1 text-xs">{testResults[source.id].detail}</p>}
                  </div>
                )}
                <ConfluenceSyncs kind="website" sourceId={source.id} syncs={syncs.filter(sync => sync.source_id === source.id)} onChanged={reload} />
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
      {creating && <WebsiteSetupDialog onClose={() => setCreating(false)} onCreated={async () => { setCreating(false); await reload(); }} />}
      {deleting && (
        <ConfirmDialog title={t('portal.connections.website.deleteDialogTitle')}
          body={<p><span className="font-semibold text-slate-950">{deleting.name}</span>{t('portal.connections.confluence.deleteDialogSuffix')}</p>}
          confirmLabel={t('portal.connections.confluence.deleteConfirm')} onClose={() => setDeleting(null)}
          onConfirm={async () => { await apiSend(`/api/v1/import/sources/${deleting.id}`, { method: 'DELETE' }); setDeleting(null); await reload(); }} />
      )}
    </div>
  );
}
