'use client';

import { useEffect, useState } from 'react';
import { CheckCircle2, KeyRound, LoaderCircle, PlugZap } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { ErrorNotice, Field, LoadingState, SectionCard, Toggle, errorMessage, inputClass } from './admin-shared';

type ChatProviderConfig = {
  configured: boolean;
  enabled: boolean;
  base_url: string;
  model: string;
  has_api_key: boolean;
  timeout_seconds: number;
  temperature: number | null;
  updated_at: string | null;
};

type TestResult = { ok: boolean; detail: string; latency_ms: number | null };
const PATH = '/api/v1/auth/admin/chat-provider';

export function ChatProviderTab() {
  const [config, setConfig] = useState<ChatProviderConfig | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiJson<ChatProviderConfig>(PATH)
      .then(value => { if (!cancelled) setConfig(value); })
      .catch(err => { if (!cancelled) setError(errorMessage(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  async function save() {
    if (!config) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    setTestResult(null);
    try {
      const next = await apiJson<ChatProviderConfig>(PATH, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: config.enabled,
          base_url: config.base_url,
          model: config.model,
          api_key: apiKey || null,
          clear_api_key: clearKey,
          timeout_seconds: config.timeout_seconds,
          temperature: config.temperature,
        }),
      });
      setConfig(next);
      setApiKey('');
      setClearKey(false);
      setMessage('Chat-Konfiguration gespeichert. Sie gilt ab der nächsten Anfrage.');
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
      setTestResult(await apiJson<TestResult>(`${PATH}/test`, { method: 'POST' }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setTesting(false);
    }
  }

  return (
    <SectionCard
      title="Chat & LLM"
      description="Zentraler OpenAI-kompatibler Endpunkt für direkte Weave-Bots. n8n-Flows verwalten ihr Modell weiterhin in n8n."
    >
      <ErrorNotice message={error} />
      {loading || !config ? <LoadingState label="Chat-Konfiguration wird geladen…" /> : (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-emerald-50 px-4 py-3">
            <Toggle checked={config.enabled} onChange={enabled => setConfig({ ...config, enabled })} label="Zentrale Chat-Generierung aktivieren" />
            <span className="flex items-center gap-2 text-xs font-medium text-emerald-800">
              {config.enabled ? <CheckCircle2 size={15} /> : <KeyRound size={15} />}
              {config.enabled ? 'Runtime verwendet diese Konfiguration' : 'Bot-Konfiguration bleibt aktiv'}
            </span>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Field label="OpenAI-kompatibler Endpoint" hint="Zum Beispiel https://llm.example.com/v1 oder http://ollama:11434/v1">
              <input className={inputClass} type="url" value={config.base_url} onChange={event => setConfig({ ...config, base_url: event.target.value })} placeholder="https://llm.example.com/v1" required={config.enabled} />
            </Field>
            <Field label="Modell" hint="Der Modellname, den der Endpoint erwartet.">
              <input className={inputClass} value={config.model} onChange={event => setConfig({ ...config, model: event.target.value })} placeholder="qwen2.5:7b-instruct" required={config.enabled} />
            </Field>
            <Field label="API-Key" hint={config.has_api_key && !clearKey ? 'Ein Key ist verschlüsselt gespeichert. Leer lassen, um ihn beizubehalten.' : 'Optional für lokale Endpoints ohne Authentifizierung.'}>
              <input className={inputClass} type="password" autoComplete="new-password" value={apiKey} disabled={clearKey} onChange={event => setApiKey(event.target.value)} placeholder={config.has_api_key ? 'Gespeicherten Key beibehalten' : 'sk-…'} />
              {config.has_api_key && <label className="mt-2 flex items-center gap-2 text-xs font-normal text-slate-600"><input type="checkbox" checked={clearKey} onChange={event => setClearKey(event.target.checked)} />Gespeicherten API-Key entfernen</label>}
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Timeout (Sekunden)">
                <input className={inputClass} type="number" min={1} max={300} value={config.timeout_seconds} onChange={event => setConfig({ ...config, timeout_seconds: Number(event.target.value) || 1 })} />
              </Field>
              <Field label="Temperatur" hint="Leer = Wert des Bots">
                <input className={inputClass} type="number" min={0} max={2} step={0.1} value={config.temperature ?? ''} onChange={event => setConfig({ ...config, temperature: event.target.value === '' ? null : Number(event.target.value) })} />
              </Field>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
            <Button onClick={save} disabled={saving}>{saving && <LoaderCircle className="h-4 w-4 animate-spin" />}{saving ? 'Wird gespeichert…' : 'Speichern'}</Button>
            <Button variant="outline" onClick={test} disabled={testing || !config.configured}>{testing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}Verbindung testen</Button>
            <span aria-live="polite" className="text-sm text-slate-600">{message}</span>
          </div>
          {testResult && <div role="status" className={`rounded-xl border px-4 py-3 text-sm ${testResult.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>{testResult.detail}{testResult.latency_ms !== null ? ` · ${testResult.latency_ms} ms` : ''}</div>}
          <p className="text-xs text-slate-500">Der API-Key wird verschlüsselt gespeichert und nie wieder im Browser angezeigt. Ein Wechsel des Endpoints ohne neuen Key entfernt den bisherigen Key zum Schutz vor versehentlicher Weitergabe.</p>
        </div>
      )}
    </SectionCard>
  );
}
