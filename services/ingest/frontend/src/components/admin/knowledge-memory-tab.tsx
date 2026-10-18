'use client';

import { useCallback, useEffect, useState } from 'react';
import { BrainCircuit, LoaderCircle, RefreshCw, Save } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { Badge, ConfirmDialog, ErrorNotice, Field, LoadingState, SectionCard, errorMessage, inputClass } from './admin-shared';
import { useI18n } from '@/i18n/provider';

// ADR-0009: Administration › KI › Wissensgedächtnis -- the knowledge-memory
// settings (projected to Weave-Knowledge and Runtime) and its metrics.

type Mode = 'off' | 'extractive' | 'llm';
type MemoryConfig = {
  mode: Mode; llm_endpoint: string; debounce_seconds: number; max_documents_per_run: number;
  max_input_chars: number; managed: boolean; updated_at: string | null;
};
type CollectionMetrics = {
  collection: string; name: string; documents: number; summarized: number; summarized_llm: number;
  overview_generator: string | null; built_at: string | null; pending: boolean; building: boolean;
  last_run_at: string | null; last_duration_ms: number | null; last_error: string | null;
  llm_calls: number; llm_failures: number;
};
export type MemoryStatus = {
  mode: string; llm_endpoint: string; documents: number; summarized: number; summarized_llm: number;
  llm_calls: number; llm_failures: number; collections_total: number; collections_built: number;
  collections_pending: number; collections_building: number; last_run_at: string | null;
  collections: CollectionMetrics[];
};

const PATH = '/api/v1/auth/admin/knowledge-memory';
const STATUS_PATH = '/api/v1/admin/knowledge-memory/status';
const REBUILD_PATH = '/api/v1/admin/knowledge-memory/rebuild';
const ENDPOINTS_PATH = '/api/v1/auth/admin/chat-provider/endpoints';
const MODES: Mode[] = ['off', 'extractive', 'llm'];
const MODE_TEXT = {
  off: { title: 'admin.memory.mode.off.title', description: 'admin.memory.mode.off.description' },
  extractive: { title: 'admin.memory.mode.extractive.title', description: 'admin.memory.mode.extractive.description' },
  llm: { title: 'admin.memory.mode.llm.title', description: 'admin.memory.mode.llm.description' },
} as const;

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '–';
}

export function KnowledgeMemoryTab() {
  return <div className="space-y-6"><KnowledgeMemorySettingsCard /><KnowledgeMemoryMetricsCard /></div>;
}

function KnowledgeMemorySettingsCard() {
  const { t } = useI18n();
  const [config, setConfig] = useState<MemoryConfig | null>(null);
  const [endpoints, setEndpoints] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiJson<MemoryConfig>(PATH).then(setConfig).catch(err => setError(errorMessage(err)));
    apiJson<{ items: { id?: string; name?: string; model: string }[] }>(ENDPOINTS_PATH)
      .then(value => setEndpoints(value.items.filter(item => item.id).map(item => ({ id: item.id as string, name: item.name || item.model }))))
      .catch(() => setEndpoints([]));
  }, []);

  if (!config) return <SectionCard title={t('admin.memory.title')} description={t('admin.memory.description')}><ErrorNotice message={error} /><LoadingState /></SectionCard>;
  const set = (patch: Partial<MemoryConfig>) => setConfig({ ...config, ...patch });
  const save = async () => {
    setSaving(true); setError(null); setMessage('');
    try {
      const next = await apiJson<MemoryConfig>(PATH, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        mode: config.mode, llm_endpoint: config.llm_endpoint, debounce_seconds: config.debounce_seconds,
        max_documents_per_run: config.max_documents_per_run, max_input_chars: config.max_input_chars,
      }) });
      setConfig(next); setMessage(t('admin.memory.saved'));
    } catch (err) { setError(errorMessage(err)); } finally { setSaving(false); }
  };

  return <SectionCard title={t('admin.memory.title')} description={t('admin.memory.description')}>
    <ErrorNotice message={error} />
    <div className="space-y-6">
      {!config.managed && <p className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">{t('admin.memory.unmanaged')}</p>}
      <fieldset>
        <legend className="text-[13px] font-semibold text-[var(--ink-2)]">{t('admin.memory.mode.label')}</legend>
        <div className="mt-2 grid gap-3 lg:grid-cols-3">
          {MODES.map(mode => (
            <label key={mode} className={`cursor-pointer rounded-xl border p-4 ${config.mode === mode ? 'border-emerald-600 bg-emerald-50/50' : 'border-slate-200'}`}>
              <span className="flex items-center gap-2 font-semibold text-slate-950">
                <input type="radio" name="memory-mode" value={mode} checked={config.mode === mode} onChange={() => set({ mode })} />
                {t(MODE_TEXT[mode].title)}
              </span>
              <span className="mt-1 block text-sm text-slate-500">{t(MODE_TEXT[mode].description)}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-4 lg:grid-cols-2">
        <Field label={t('admin.memory.field.endpoint.label')} hint={t('admin.memory.field.endpoint.hint')}>
          <select className={inputClass} value={config.llm_endpoint} onChange={e => set({ llm_endpoint: e.target.value })} disabled={config.mode !== 'llm'}>
            <option value="default">{t('admin.memory.field.endpoint.default')}</option>
            {endpoints.filter(item => item.id !== 'default').map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </Field>
        <Field label={t('admin.memory.field.debounce.label')} hint={t('admin.memory.field.debounce.hint')}>
          <input className={inputClass} type="number" min={10} max={86400} value={config.debounce_seconds} onChange={e => set({ debounce_seconds: Number(e.target.value) })} />
        </Field>
        <Field label={t('admin.memory.field.batch.label')} hint={t('admin.memory.field.batch.hint')}>
          <input className={inputClass} type="number" min={1} max={500} value={config.max_documents_per_run} onChange={e => set({ max_documents_per_run: Number(e.target.value) })} />
        </Field>
        <Field label={t('admin.memory.field.input.label')} hint={t('admin.memory.field.input.hint')}>
          <input className={inputClass} type="number" min={1000} max={200000} step={1000} value={config.max_input_chars} onChange={e => set({ max_input_chars: Number(e.target.value) })} disabled={config.mode !== 'llm'} />
        </Field>
      </div>
      <div className="flex items-center gap-3 border-t border-slate-100 pt-4">
        <Button onClick={save} disabled={saving}>{saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}{t('common.save')}</Button>
        <span role="status" className="text-sm text-slate-600">{message}</span>
      </div>
    </div>
  </SectionCard>;
}

function Metric({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <div className="rounded-xl border border-slate-200 p-4">
    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
    <p className="mt-1 text-2xl font-semibold text-slate-950">{value}</p>
    {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
  </div>;
}

export function KnowledgeMemoryMetricsCard() {
  const { t } = useI18n();
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchStatus = useCallback(
    () => apiJson<MemoryStatus>(STATUS_PATH).then(setStatus).catch(err => setError(errorMessage(err))),
    [],
  );
  useEffect(() => { fetchStatus(); }, [fetchStatus]);
  const load = () => {
    setLoading(true); setError(null);
    fetchStatus().finally(() => setLoading(false));
  };

  const coverage = status && status.documents ? Math.round((status.summarized / status.documents) * 100) : 0;
  const actions = <>
    <Button variant="outline" onClick={load} disabled={loading}>{loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}{t('admin.memory.metrics.refresh')}</Button>
    <Button variant="outline" onClick={() => setConfirming(true)}><BrainCircuit className="h-4 w-4" />{t('admin.memory.rebuild.title')}</Button>
  </>;

  return <SectionCard title={t('admin.memory.metrics.title')} description={t('admin.memory.metrics.description')} actions={actions}>
    <ErrorNotice message={error} />
    {notice && <p role="status" className="mb-4 text-sm text-slate-600">{notice}</p>}
    {!status && !error && <LoadingState />}
    {status && <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label={t('admin.memory.metrics.coverage')} value={`${coverage} %`} hint={t('admin.memory.metrics.coverageHint', { summarized: status.summarized, documents: status.documents })} />
        <Metric label={t('admin.memory.metrics.spaces')} value={`${status.collections_built} / ${status.collections_total}`} hint={t('admin.memory.metrics.spacesHint', { pending: status.collections_pending, building: status.collections_building })} />
        <Metric label={t('admin.memory.metrics.llm')} value={status.summarized_llm} hint={t('admin.memory.metrics.llmHint', { extractive: status.summarized - status.summarized_llm })} />
        <Metric label={t('admin.memory.metrics.calls')} value={status.llm_calls} hint={t('admin.memory.metrics.callsHint', { failures: status.llm_failures })} />
      </div>
      <p className="text-xs text-slate-500">{t('admin.memory.metrics.effective', { mode: t(MODE_TEXT[MODES.includes(status.mode as Mode) ? status.mode as Mode : 'off'].title), lastRun: formatDate(status.last_run_at) })}</p>
      {status.collections.length === 0 ? <p className="text-sm text-slate-500">{t('admin.memory.metrics.empty')}</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-4">{t('admin.memory.metrics.col.space')}</th>
                <th className="py-2 pr-4">{t('admin.memory.metrics.col.documents')}</th>
                <th className="py-2 pr-4">{t('admin.memory.metrics.col.overview')}</th>
                <th className="py-2 pr-4">{t('admin.memory.metrics.col.lastRun')}</th>
                <th className="py-2 pr-4">{t('admin.memory.metrics.col.calls')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {status.collections.map(item => (
                <tr key={item.collection}>
                  <td className="py-2 pr-4 font-medium text-slate-950">{item.name}</td>
                  <td className="py-2 pr-4">{t('admin.memory.metrics.summarizedOf', { summarized: item.summarized, documents: item.documents })}</td>
                  <td className="py-2 pr-4">
                    {item.building ? <Badge tone="amber">{t('admin.memory.state.building')}</Badge>
                      : item.pending ? <Badge tone="amber">{t('admin.memory.state.pending')}</Badge>
                        : item.built_at ? <Badge tone="emerald">{item.overview_generator === 'llm' ? t('admin.memory.state.llm') : t('admin.memory.state.extractive')}</Badge>
                          : <Badge tone="slate">{t('admin.memory.state.none')}</Badge>}
                  </td>
                  <td className="py-2 pr-4">
                    {formatDate(item.last_run_at)}{item.last_duration_ms !== null && ` · ${(item.last_duration_ms / 1000).toFixed(1)} s`}
                    {item.last_error && <span className="mt-0.5 block text-xs text-red-600">{item.last_error}</span>}
                  </td>
                  <td className="py-2 pr-4">{t('admin.memory.metrics.callsCell', { calls: item.llm_calls, failures: item.llm_failures })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>}
    {confirming && (
      <ConfirmDialog
        title={t('admin.memory.rebuild.title')}
        body={<p>{t('admin.memory.rebuild.confirmBody')}</p>}
        confirmLabel={t('admin.memory.rebuild.confirmLabel')}
        onClose={() => setConfirming(false)}
        onConfirm={async () => {
          const result = await apiJson<{ queued: number }>(REBUILD_PATH, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
          setNotice(t('admin.memory.rebuild.started', { count: result.queued }));
          setConfirming(false);
          load();
        }}
      />
    )}
  </SectionCard>;
}
