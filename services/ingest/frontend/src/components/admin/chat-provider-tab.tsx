'use client';

import { useEffect, useState } from 'react';
import { CheckCircle2, KeyRound, LoaderCircle, PlugZap, Plus, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { ErrorNotice, Field, LoadingState, SectionCard, Toggle, apiSend, errorMessage, inputClass } from './admin-shared';
import { useI18n } from '@/i18n/provider';

export type ChatProviderConfig = {
  id?: string;
  name?: string;
  configured: boolean;
  enabled: boolean;
  base_url: string;
  model: string;
  has_api_key: boolean;
  timeout_seconds: number;
  temperature: number | null;
  supports_tools: boolean;
  updated_at: string | null;
};

type TestResult = { ok: boolean; detail: string; latency_ms: number | null };
const PATH = '/api/v1/auth/admin/chat-provider';
const ENDPOINT_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Edits one LLM endpoint: the central 'default' (loaded here) or, from
 * `ChatEndpointsSection`, a named one passed in as `initial`. */
export function ChatProviderTab({
  endpointId = 'default',
  initial,
  onChanged,
}: { endpointId?: string; initial?: ChatProviderConfig; onChanged?: () => void } = {}) {
  const { t } = useI18n();
  const isDefault = endpointId === 'default';
  const path = isDefault ? PATH : `${PATH}/endpoints/${encodeURIComponent(endpointId)}`;
  const [config, setConfig] = useState<ChatProviderConfig | null>(initial ?? null);
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [loading, setLoading] = useState(!initial);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    apiJson<ChatProviderConfig>(PATH)
      .then(value => { if (!cancelled) setConfig(value); })
      .catch(err => { if (!cancelled) setError(errorMessage(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [initial]);

  async function save() {
    if (!config) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    setTestResult(null);
    try {
      const next = await apiJson<ChatProviderConfig>(path, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: config.name ?? '',
          enabled: config.enabled,
          base_url: config.base_url,
          model: config.model,
          api_key: apiKey || null,
          clear_api_key: clearKey,
          timeout_seconds: config.timeout_seconds,
          temperature: config.temperature,
          supports_tools: config.supports_tools,
        }),
      });
      setConfig(next);
      setApiKey('');
      setClearKey(false);
      setMessage(t('admin.chatProvider.saved'));
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setError(null);
    setTestResult(null);
    try {
      setTestResult(await apiJson<TestResult>(`${path}/test`, { method: 'POST' }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setTesting(false);
    }
  }

  async function remove() {
    setError(null);
    try {
      await apiSend(path, { method: 'DELETE' });
      onChanged?.();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <SectionCard
      title={isDefault ? t('admin.chatProvider.title') : (config?.name || endpointId)}
      description={isDefault ? t('admin.chatProvider.description') : t('admin.chatProvider.endpoints.itemDescription', { id: endpointId })}
    >
      <ErrorNotice message={error} />
      {loading || !config ? <LoadingState label={t('admin.chatProvider.loading')} /> : (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-emerald-50 px-4 py-3">
            <Toggle checked={config.enabled} onChange={enabled => setConfig({ ...config, enabled })} label={isDefault ? t('admin.chatProvider.toggle.enable') : t('admin.chatProvider.endpoints.toggle')} />
            {isDefault && (
              <span className="flex items-center gap-2 text-xs font-medium text-emerald-800">
                {config.enabled ? <CheckCircle2 size={15} /> : <KeyRound size={15} />}
                {config.enabled ? t('admin.chatProvider.status.usingConfig') : t('admin.chatProvider.status.usingBotConfig')}
              </span>
            )}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {!isDefault && (
              <Field label={t('admin.chatProvider.endpoints.name.label')} hint={t('admin.chatProvider.endpoints.name.hint')}>
                <input className={inputClass} value={config.name ?? ''} onChange={event => setConfig({ ...config, name: event.target.value })} maxLength={255} />
              </Field>
            )}
            <Field label={t('admin.chatProvider.field.endpoint.label')} hint={t('admin.chatProvider.field.endpoint.hint')}>
              <input className={inputClass} type="url" value={config.base_url} onChange={event => setConfig({ ...config, base_url: event.target.value })} placeholder="https://llm.example.com/v1" required={config.enabled} />
            </Field>
            <Field label={t('admin.chatProvider.field.model.label')} hint={t('admin.chatProvider.field.model.hint')}>
              <input className={inputClass} value={config.model} onChange={event => setConfig({ ...config, model: event.target.value })} placeholder="qwen2.5:7b-instruct" required={config.enabled} />
            </Field>
            <Field label={t('admin.chatProvider.field.apiKey.label')} hint={config.has_api_key && !clearKey ? t('admin.chatProvider.field.apiKey.hintStored') : t('admin.chatProvider.field.apiKey.hintOptional')}>
              <input className={inputClass} type="password" autoComplete="new-password" value={apiKey} disabled={clearKey} onChange={event => setApiKey(event.target.value)} placeholder={config.has_api_key ? t('admin.chatProvider.field.apiKey.placeholderKeep') : 'sk-…'} />
              {config.has_api_key && <label className="mt-2 flex items-center gap-2 text-xs font-normal text-slate-600"><input type="checkbox" checked={clearKey} onChange={event => setClearKey(event.target.checked)} />{t('admin.chatProvider.field.apiKey.clear')}</label>}
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('admin.chatProvider.field.timeout')}>
                <input className={inputClass} type="number" min={1} max={300} value={config.timeout_seconds} onChange={event => setConfig({ ...config, timeout_seconds: Number(event.target.value) || 1 })} />
              </Field>
              <Field label={t('admin.chatProvider.field.temperature.label')} hint={t('admin.chatProvider.field.temperature.hint')}>
                <input className={inputClass} type="number" min={0} max={2} step={0.1} value={config.temperature ?? ''} onChange={event => setConfig({ ...config, temperature: event.target.value === '' ? null : Number(event.target.value) })} />
              </Field>
            </div>
          </div>

          <Toggle
            checked={config.supports_tools}
            onChange={supports_tools => setConfig({ ...config, supports_tools })}
            label={t('admin.chatProvider.toggle.supportsTools')}
          />

          <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
            <Button onClick={save} disabled={saving}>{saving && <LoaderCircle className="h-4 w-4 animate-spin" />}{saving ? t('admin.chatProvider.saving') : t('common.save')}</Button>
            <Button variant="outline" onClick={test} disabled={testing || !config.configured}>{testing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}{t('admin.chatProvider.testConnection')}</Button>
            {!isDefault && config.configured && <Button variant="outline" onClick={remove}><Trash2 className="h-4 w-4" />{t('admin.chatProvider.endpoints.delete')}</Button>}
            <span aria-live="polite" className="text-sm text-slate-600">{message}</span>
          </div>
          {testResult && <div role="status" className={`rounded-xl border px-4 py-3 text-sm ${testResult.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>{testResult.detail}{testResult.latency_ms !== null ? ` · ${testResult.latency_ms} ms` : ''}</div>}
          <p className="text-xs text-slate-500">{t('admin.chatProvider.apiKeyNote')}</p>
        </div>
      )}
    </SectionCard>
  );
}

function blankEndpoint(id: string): ChatProviderConfig {
  return {
    id, name: '', configured: false, enabled: true, base_url: '', model: '', has_api_key: false,
    timeout_seconds: 60, temperature: null, supports_tools: false, updated_at: null,
  };
}

/** Further named LLM endpoints next to the central one: a bot can answer
 * with one of them (e.g. a model with native tool calling) or offer several
 * to its users (Bots > LLM-Endpunkt). */
export function ChatEndpointsSection() {
  const { t } = useI18n();
  const [items, setItems] = useState<ChatProviderConfig[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [newId, setNewId] = useState('');
  const [error, setError] = useState<string | null>(null);

  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    apiJson<{ items: ChatProviderConfig[] }>(`${PATH}/endpoints`)
      .then(result => {
        if (cancelled) return;
        const named = result.items.filter(item => item.id !== 'default');
        setItems(named);
        setSelected(current => (current && named.some(item => item.id === current) ? current : null));
      })
      .catch(err => { if (!cancelled) setError(errorMessage(err)); });
    return () => { cancelled = true; };
  }, [version]);

  function add() {
    const id = newId.trim().toLowerCase();
    if (!ENDPOINT_ID.test(id) || id === 'default' || id.length > 36) {
      setError(t('admin.chatProvider.endpoints.invalidId'));
      return;
    }
    setError(null);
    if (!items?.some(item => item.id === id)) setItems([...(items ?? []), blankEndpoint(id)]);
    setSelected(id);
    setNewId('');
  }

  const current = items?.find(item => item.id === selected);
  return (
    <div className="mt-6 space-y-4">
      <SectionCard title={t('admin.chatProvider.endpoints.title')} description={t('admin.chatProvider.endpoints.description')}>
        <ErrorNotice message={error} />
        {items === null ? <LoadingState label={t('admin.chatProvider.loading')} /> : (
          <div className="space-y-4">
            {items.length === 0 ? <p className="text-sm text-slate-500">{t('admin.chatProvider.endpoints.empty')}</p> : (
              <div className="flex flex-wrap gap-2">
                {items.map(item => (
                  <Button key={item.id} variant={item.id === selected ? 'default' : 'outline'} onClick={() => setSelected(item.id ?? null)}>
                    {item.name || item.id}{item.model ? ` · ${item.model}` : ''}{item.enabled ? '' : ` (${t('admin.chatProvider.endpoints.disabled')})`}
                  </Button>
                ))}
              </div>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <Field label={t('admin.chatProvider.endpoints.id.label')} hint={t('admin.chatProvider.endpoints.id.hint')}>
                <input className={inputClass} value={newId} onChange={event => setNewId(event.target.value)} placeholder="tools-llm" maxLength={36} />
              </Field>
              <Button variant="outline" onClick={add}><Plus className="h-4 w-4" />{t('admin.chatProvider.endpoints.add')}</Button>
            </div>
          </div>
        )}
      </SectionCard>
      {current?.id && <ChatProviderTab key={current.id} endpointId={current.id} initial={current} onChanged={() => setVersion(value => value + 1)} />}
    </div>
  );
}
