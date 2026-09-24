'use client';

import { useCallback, useEffect, useState } from 'react';
import { Bot, CircleCheck, CircleX, LoaderCircle, Pencil, PlugZap, Plus, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiJson } from '@/lib/api';
import type { ListResponse } from '@/lib/auth-types';
import { useI18n } from '@/i18n/provider';
import {
  apiSend,
  Badge,
  ConfirmDialog,
  ErrorNotice,
  errorMessage,
  Field,
  inputClass,
  LoadingState,
  Modal,
  SectionCard,
  Toggle,
} from '@/components/admin/admin-shared';

/**
 * TypeScript mirrors of the backend VL-connection admin schemas
 * (backend/app/api/v1/auth/admin — /api/v1/auth/admin/vl-connections).
 * Kept local to this file rather than in lib/auth-types.ts per this
 * round's file-ownership split.
 */

/** VlConnectionAdminResponse — one item from /api/v1/auth/admin/vl-connections. */
interface AdminVlConnection {
  id: string;
  name: string;
  base_url: string;
  model: string;
  /** Write-only key: responses only report whether one is stored. */
  has_api_key: boolean;
  system_prompt: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/** POST /api/v1/auth/admin/vl-connections */
interface VlConnectionCreateRequest {
  name: string;
  base_url: string;
  model: string;
  api_key: string;
  system_prompt?: string;
  enabled?: boolean;
}

/** PUT /api/v1/auth/admin/vl-connections/{id} — api_key is write-only; omit to keep the stored key. */
interface VlConnectionUpdateRequest {
  name?: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  system_prompt?: string;
  enabled?: boolean;
}

/** POST /api/v1/auth/admin/vl-connections/{id}/test */
interface VlConnectionTestResponse {
  ok: boolean;
  detail?: string | null;
  latency_ms?: number | null;
}

const BASE = '/api/v1/auth/admin/vl-connections';

export function VlConnectionsTab() {
  const { t, formatDate } = useI18n();
  const [connections, setConnections] = useState<AdminVlConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  // 404 means the backend hasn't shipped this endpoint yet, not a real
  // failure — rendered as a distinct, non-alarming notice (mirrors LogsTab).
  const [unavailable, setUnavailable] = useState(false);

  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<AdminVlConnection | null>(null);
  const [deleting, setDeleting] = useState<AdminVlConnection | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, VlConnectionTestResponse>>({});

  const reload = useCallback(async () => {
    try {
      const data = await apiJson<ListResponse<AdminVlConnection>>(BASE);
      setConnections(data.items);
      setListError(null);
      setUnavailable(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setUnavailable(true);
        setListError(null);
      } else {
        setListError(errorMessage(err));
        setUnavailable(false);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function testConnection(id: string) {
    setTesting(id);
    setTestResults((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    try {
      const res = await apiJson<VlConnectionTestResponse>(`${BASE}/${id}/test`, { method: 'POST' });
      setTestResults((prev) => ({ ...prev, [id]: res }));
    } catch (err) {
      setTestResults((prev) => ({ ...prev, [id]: { ok: false, detail: errorMessage(err) } }));
    } finally {
      setTesting(null);
    }
  }

  return (
    <div className="space-y-6">
      <SectionCard
        title={t('admin.vlConnections.title')}
        description={t('admin.vlConnections.description')}
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            {t('admin.vlConnections.addConnection')}
          </Button>
        }
      >
        <ErrorNotice message={listError} />
        {unavailable && (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {t('admin.vlConnections.unavailable')}
          </div>
        )}
        {loading ? (
          <LoadingState label={t('admin.vlConnections.loading')} />
        ) : unavailable ? null : connections.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <Bot className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-500">{t('admin.vlConnections.empty')}</p>
            <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" />
              {t('admin.vlConnections.addConnection')}
            </Button>
          </div>
        ) : (
          <ul className="space-y-4">
            {connections.map((c) => (
              <li key={c.id} className="rounded-xl border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-slate-950">{c.name}</span>
                      <Badge tone={c.enabled ? 'emerald' : 'slate'}>
                        {c.enabled ? t('admin.vlConnections.enabled') : t('admin.vlConnections.disabled')}
                      </Badge>
                      <Badge tone={c.has_api_key ? 'emerald' : 'amber'}>
                        {c.has_api_key ? t('admin.vlConnections.tokenSet') : t('admin.vlConnections.noToken')}
                      </Badge>
                    </div>
                    <dl className="mt-2 space-y-1 text-xs text-slate-500">
                      <div className="flex gap-2">
                        <dt className="w-16 flex-shrink-0 font-medium">{t('admin.vlConnections.baseUrlLabel')}</dt>
                        <dd className="break-all">{c.base_url}</dd>
                      </div>
                      <div className="flex gap-2">
                        <dt className="w-16 flex-shrink-0 font-medium">{t('admin.vlConnections.modelLabel')}</dt>
                        <dd className="break-all font-mono">{c.model}</dd>
                      </div>
                      <div className="flex gap-2">
                        <dt className="w-16 flex-shrink-0 font-medium">{t('admin.vlConnections.createdLabel')}</dt>
                        <dd>{formatDate(c.created_at)}</dd>
                      </div>
                    </dl>
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => testConnection(c.id)}
                      disabled={testing !== null}
                    >
                      {testing === c.id ? (
                        <LoaderCircle className="h-4 w-4 animate-spin" />
                      ) : (
                        <PlugZap className="h-4 w-4" />
                      )}
                      {t('admin.vlConnections.test')}
                    </Button>
                    <button
                      onClick={() => setEditing(c)}
                      aria-label={t('admin.vlConnections.editAria', { name: c.name })}
                      title={t('common.edit')}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-slate-700"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => setDeleting(c)}
                      aria-label={t('admin.vlConnections.deleteAria', { name: c.name })}
                      title={t('common.delete')}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-50 hover:text-red-600"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
                {testResults[c.id] && <VlTestResult result={testResults[c.id]} />}
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      {creating && (
        <VlConnectionModal
          onClose={() => setCreating(false)}
          onSaved={async () => {
            setCreating(false);
            await reload();
          }}
        />
      )}

      {editing && (
        <VlConnectionModal
          connection={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await reload();
          }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={t('admin.vlConnections.deleteDialogTitle')}
          body={<p>{t('admin.vlConnections.deleteDialogBody', { name: deleting.name })}</p>}
          confirmLabel={t('admin.vlConnections.deleteConfirm')}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await apiSend(`${BASE}/${deleting.id}`, { method: 'DELETE' });
            setDeleting(null);
            await reload();
          }}
        />
      )}
    </div>
  );
}

function VlTestResult({ result }: { result: VlConnectionTestResponse }) {
  const { t } = useI18n();
  return (
    <div
      className={`mt-3 rounded-xl border px-4 py-3 text-sm ${
        result.ok
          ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
          : 'border-red-200 bg-red-50 text-red-700'
      }`}
    >
      <div className="flex items-center gap-2 font-medium">
        {result.ok ? (
          <CircleCheck className="h-4 w-4 flex-shrink-0" />
        ) : (
          <CircleX className="h-4 w-4 flex-shrink-0" />
        )}
        {result.ok ? t('admin.vlConnections.testSuccess') : t('admin.vlConnections.testFailed')}
      </div>
      {result.detail && <p className="mt-1 text-xs">{result.detail}</p>}
      {result.ok && result.latency_ms != null && (
        <p className="mt-1 text-xs">{result.latency_ms} ms</p>
      )}
    </div>
  );
}

/** Create (no `connection`) or edit (with `connection`) a VL connection. */
function VlConnectionModal({
  connection,
  onClose,
  onSaved,
}: {
  connection?: AdminVlConnection;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const isEdit = connection !== undefined;

  const [name, setName] = useState(connection?.name ?? '');
  const [baseUrl, setBaseUrl] = useState(connection?.base_url ?? '');
  const [model, setModel] = useState(connection?.model ?? '');
  const [apiKey, setApiKey] = useState('');
  const [systemPrompt, setSystemPrompt] = useState(connection?.system_prompt ?? '');
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (isEdit) {
        const body: VlConnectionUpdateRequest = {
          name: name.trim(),
          base_url: baseUrl.trim(),
          model: model.trim(),
          system_prompt: systemPrompt.trim(),
          enabled,
          ...(apiKey ? { api_key: apiKey } : {}),
        };
        await apiJson<AdminVlConnection>(`${BASE}/${connection.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } else {
        const body: VlConnectionCreateRequest = {
          name: name.trim(),
          base_url: baseUrl.trim(),
          model: model.trim(),
          api_key: apiKey,
          system_prompt: systemPrompt.trim(),
          enabled,
        };
        await apiJson<AdminVlConnection>(BASE, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      }
      await onSaved();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={isEdit ? t('admin.vlConnections.editModalTitle', { name: connection.name }) : t('admin.vlConnections.addModalTitle')}
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label={t('admin.vlConnections.fieldName')}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
            required
            autoFocus
          />
        </Field>
        <Field label={t('admin.vlConnections.baseUrlLabel')} hint={t('admin.vlConnections.baseUrlHint')}>
          <input
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className={inputClass}
            required
            placeholder={t('admin.vlConnections.baseUrlPlaceholder')}
          />
        </Field>
        <Field label={t('admin.vlConnections.modelLabel')}>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className={inputClass}
            required
            placeholder={t('admin.vlConnections.modelPlaceholder')}
          />
        </Field>
        <Field label={t('admin.vlConnections.tokenLabel')} hint={isEdit ? t('admin.vlConnections.tokenHintEdit') : undefined}>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className={inputClass}
            required={!isEdit}
            placeholder={isEdit ? t('admin.vlConnections.tokenPlaceholderEdit') : undefined}
            autoComplete="new-password"
          />
        </Field>
        <Field label={t('admin.vlConnections.systemPromptLabel')} hint={t('admin.vlConnections.systemPromptHint')}>
          <textarea
            rows={4}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            className={inputClass}
          />
        </Field>
        <Toggle checked={enabled} onChange={setEnabled} label={t('admin.vlConnections.enabled')} />
        <ErrorNotice message={error} />
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" size="sm" disabled={busy}>
            {busy && <LoaderCircle className="h-4 w-4 animate-spin" />}
            {isEdit ? t('admin.vlConnections.saveChanges') : t('admin.vlConnections.addConnection')}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
