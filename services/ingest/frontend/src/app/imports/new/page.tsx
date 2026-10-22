'use client';

import { Suspense, useEffect, useEffectEvent, useState } from 'react';
import Link from 'next/link';
import { AnimatePresence, motion } from 'framer-motion';
import { CheckCircle2, LoaderCircle, XCircle } from 'lucide-react';
import { useRouter, useSearchParams } from 'next/navigation';

import { Button } from '@/components/ui/button';
import { BackLink } from '@/components/back-link';
import { PortalPage } from '@/components/portal/shared';
import { Toggle } from '@/components/admin/admin-shared';
import { ApiError, apiJson } from '@/lib/api';
import { type PaddleCapabilities } from '@/components/dashboard/shared';
import {
  formatRefreshInterval,
  refreshIntervalOptions,
  type ImportAuthType,
  type ImportRun,
  type ImportRunDetail,
  type ImportRunOptions,
  type ImportSource,
  type ImportSourceListResponse,
  type ImportSourceTestResponse,
  type ImportSync,
  TEST_COOLDOWN_FALLBACK_MS,
} from '@/lib/imports';
import { loadWritableSpaces, type KnowledgeSpace } from '@/lib/portal';
import { listWebhookConnections, type WebhookConnection } from '@/lib/webhooks';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';

function wizardSteps(t: (key: MessageKey) => string) {
  return [
    { id: 1, title: t('portal.importWizard.step1.title'), description: t('portal.importWizard.step1.description') },
    { id: 2, title: t('portal.importWizard.step2.title'), description: t('portal.importWizard.step2.description') },
    { id: 3, title: t('portal.importWizard.step3.title'), description: t('portal.importWizard.step3.description') },
  ];
}

// Mirrors the backend defaults (import_max_pages / import_max_depth); the
// server clamps whatever the client sends, these are display hints only.
const SERVER_DEFAULT_MAX_PAGES = 200;
const SERVER_DEFAULT_MAX_DEPTH = 10;

type ImportRunDetailWithPrefill = ImportRunDetail & {
  source_id: string | null;
  options: ImportRunOptions;
};

// Daily: the preset "Quelle hinzufügen" uses too.
const DEFAULT_SYNC_INTERVAL_SECONDS = 86400;

export default function NewImportPage() {
  return (
    <Suspense fallback={<main className="portal-page" />}>
      <NewImportPageInner />
    </Suspense>
  );
}

function NewImportPageInner() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const prefillFromRunId = searchParams.get('from');
  // ?sync=<id> edits an import configuration in place; ?source=<id> starts a
  // new import on that connection (both linked from Verbindungen › Confluence).
  const editSyncId = searchParams.get('sync');
  const presetSourceId = searchParams.get('source');

  const [wizardStep, setWizardStep] = useState(1);
  const [prefillNotice, setPrefillNotice] = useState<string | null>(null);

  // --- Step 1: connection ---
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [sourcesLoaded, setSourcesLoaded] = useState(false);
  const [sourcesLoadFailed, setSourcesLoadFailed] = useState(false);
  const [loadNonce, setLoadNonce] = useState(0);
  const [selectedSourceId, setSelectedSourceId] = useState('');
  const [creatingSource, setCreatingSource] = useState(false);

  const [newName, setNewName] = useState('');
  const [newBaseUrl, setNewBaseUrl] = useState('');
  // Server / Data Center (PAT) is the common on-premises case, so it is preselected.
  const [newAuthType, setNewAuthType] = useState<ImportAuthType>('pat_bearer');
  const [newEmail, setNewEmail] = useState('');
  // Write-only secrets: only ever hold what the user is typing right now;
  // stored credentials are never fetched or displayed.
  const [newCredential, setNewCredential] = useState('');
  const [createBusy, setCreateBusy] = useState(false);
  const [connectionMessage, setConnectionMessage] = useState<string | null>(null);

  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState<ImportSourceTestResponse | null>(null);
  // The server cooldown is per source (last_test_at column), so track one
  // client-side deadline per source id instead of a single global timer.
  const [testCooldowns, setTestCooldowns] = useState<Record<string, number>>({});
  // Remaining seconds for the SELECTED source's cooldown; updated from event
  // handlers and the interval below (never during render — purity rule).
  const [cooldownSeconds, setCooldownSeconds] = useState(0);

  // --- Step 2: scope ---
  const [scopeType, setScopeType] = useState<'space' | 'page'>('space');
  const [scopeValue, setScopeValue] = useState('');

  // --- Step 3: target, schedule, options ---
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [syncEnabled, setSyncEnabled] = useState(false);
  const [syncInterval, setSyncInterval] = useState(DEFAULT_SYNC_INTERVAL_SECONDS);
  // The configuration being edited: scope and space are then fixed.
  const [editingSync, setEditingSync] = useState<ImportSync | null>(null);
  const [maxPages, setMaxPages] = useState('');
  const [maxDepth, setMaxDepth] = useState('');
  const [includeAttachments, setIncludeAttachments] = useState(true);
  const [ocrAttachments, setOcrAttachments] = useState(false);
  const [pathTags, setPathTags] = useState(false);
  const [capabilities, setCapabilities] = useState<PaddleCapabilities>({ profiles: [] });
  const [selectedProfileId, setSelectedProfileId] = useState('');
  // '' = no webhook target; only enabled connections are offered.
  const [webhookConnections, setWebhookConnections] = useState<WebhookConnection[]>([]);
  const [webhookConnectionId, setWebhookConnectionId] = useState('');

  const [tags, setTags] = useState('');
  const [email, setEmail] = useState('');

  const [startBusy, setStartBusy] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const selectedSource = sources.find((entry) => entry.id === selectedSourceId) ?? null;
  const intervalOptions = refreshIntervalOptions(locale);

  // Current-language messages without making `t` an effect dependency: a
  // language switch must not re-run the load and reset the wizard.
  const message = useEffectEvent((key: MessageKey) => t(key));

  useEffect(() => {
    let cancelled = false;

    const loadInitialData = async () => {
      try {
        const [sourcesPayload, spacesPayload, capabilitiesPayload, webhookConnectionsPayload, prefillRun, requestedSync] = await Promise.all([
          apiJson<ImportSourceListResponse>('/api/v1/import/sources', { cache: 'no-store' }),
          loadWritableSpaces().catch(() => [] as KnowledgeSpace[]),
          apiJson<PaddleCapabilities>('/api/v1/paddle/capabilities', { cache: 'no-store' }).catch(
            () => ({ profiles: [] }) as PaddleCapabilities,
          ),
          listWebhookConnections().catch(() => ({ items: [] as WebhookConnection[] })),
          prefillFromRunId
            ? apiJson<ImportRunDetailWithPrefill>(`/api/v1/import/runs/${prefillFromRunId}`, { cache: 'no-store' }).catch(
                () => null,
              )
            : Promise.resolve(null),
          editSyncId
            ? apiJson<ImportSync>(`/api/v1/import/syncs/${editSyncId}`, { cache: 'no-store' }).catch(() => null)
            : Promise.resolve(null),
        ]);
        // A run that belongs to a configuration is edited as that configuration.
        const syncToEdit =
          requestedSync ??
          (prefillRun?.sync_id
            ? await apiJson<ImportSync>(`/api/v1/import/syncs/${prefillRun.sync_id}`, { cache: 'no-store' }).catch(() => null)
            : null);
        if (cancelled) return;
        setSources(sourcesPayload.items);
        setSpaces(spacesPayload);

        const prefillSourceStillExists =
          prefillRun?.source_id != null && sourcesPayload.items.some((entry) => entry.id === prefillRun.source_id);

        if (syncToEdit) {
          setSelectedSourceId(syncToEdit.source_id);
        } else if (prefillRun && prefillSourceStillExists) {
          setSelectedSourceId(prefillRun.source_id as string);
        } else if (presetSourceId && sourcesPayload.items.some((entry) => entry.id === presetSourceId)) {
          setSelectedSourceId(presetSourceId);
          setWizardStep(2);
        } else if (sourcesPayload.items.length > 0) {
          setSelectedSourceId(sourcesPayload.items[0].id);
        } else {
          setCreatingSource(true);
        }

        const profiles = capabilitiesPayload.profiles ?? [];
        setCapabilities({ profiles });
        setWebhookConnections(webhookConnectionsPayload.items.filter((connection) => connection.enabled));
        const prefillOptions = syncToEdit?.options ?? prefillRun?.options ?? null;
        const prefillProfileId = prefillOptions?.ocr_profile_id ?? null;
        if (prefillProfileId && profiles.some((profile) => profile.value === prefillProfileId)) {
          setSelectedProfileId(prefillProfileId);
        } else if (profiles.length > 0) {
          setSelectedProfileId(profiles[0].value);
        }

        const prefill = syncToEdit ?? prefillRun;
        if (prefill && prefillOptions) {
          setScopeType(prefill.scope_type === 'page' ? 'page' : 'space');
          setScopeValue(prefill.scope_value);
          const options = prefillOptions;
          setCollectionId(options.collection_id ?? null);
          setMaxPages(options.max_pages != null ? String(options.max_pages) : '');
          setMaxDepth(options.max_depth != null ? String(options.max_depth) : '');
          setIncludeAttachments(options.include_attachments);
          setOcrAttachments(options.ocr_attachments);
          setPathTags(options.path_tags ?? false);
          setTags((options.tags ?? []).join(', '));
          setEmail(options.email ?? '');
          setWebhookConnectionId(webhookConnectionsPayload.items.some(connection => connection.enabled && connection.id === options.webhook_connection_id) ? options.webhook_connection_id ?? '' : '');
          if (syncToEdit) {
            setEditingSync(syncToEdit);
            setSyncEnabled(syncToEdit.enabled);
            setSyncInterval(syncToEdit.interval_seconds ?? DEFAULT_SYNC_INTERVAL_SECONDS);
            setWizardStep(3);
          } else if (prefillSourceStillExists) {
            setWizardStep(3);
            setPrefillNotice(message('portal.importWizard.prefillReused'));
          } else {
            setConnectionMessage(message('portal.importWizard.prefillSourceMissing'));
            setPrefillNotice(message('portal.importWizard.prefillReused'));
          }
        } else if (prefillFromRunId || editSyncId) {
          setPrefillNotice(message('portal.importWizard.prefillFailed'));
        }
      } catch (error) {
        if (!cancelled) {
          setSourcesLoadFailed(true);
          setConnectionMessage(error instanceof ApiError ? error.detail : message('portal.importWizard.error.loadSourcesFailed'));
        }
      } finally {
        if (!cancelled) setSourcesLoaded(true);
      }
    };

    void loadInitialData();
    return () => {
      cancelled = true;
    };
  }, [loadNonce, prefillFromRunId, editSyncId, presetSourceId]);

  const retryLoadSources = () => {
    setSourcesLoaded(false);
    setSourcesLoadFailed(false);
    setConnectionMessage(null);
    setLoadNonce((nonce) => nonce + 1);
  };

  // Countdown for the selected source's /test cooldown window. Runs an
  // immediate async update (covers switching between sources with different
  // deadlines) plus a 250 ms tick while a deadline exists, and drops the
  // entry once it passes so the Test button re-enables.
  useEffect(() => {
    if (!selectedSourceId) return;
    const until = testCooldowns[selectedSourceId];
    const update = () => {
      if (until === undefined) {
        setCooldownSeconds(0);
        return;
      }
      const remaining = Math.ceil((until - Date.now()) / 1000);
      if (remaining <= 0) {
        setTestCooldowns((current) => {
          const next = { ...current };
          delete next[selectedSourceId];
          return next;
        });
        setCooldownSeconds(0);
      } else {
        setCooldownSeconds(remaining);
      }
    };
    const immediate = window.setTimeout(update, 0);
    const timer = until !== undefined ? window.setInterval(update, 250) : null;
    return () => {
      window.clearTimeout(immediate);
      if (timer !== null) window.clearInterval(timer);
    };
  }, [selectedSourceId, testCooldowns]);

  // The backend claims the per-source cooldown slot on EVERY completed test
  // attempt (last_test_at is set before probing, success or failure), so the
  // client arms its fallback window after each completed test as well as on
  // an explicit 429.
  const armTestCooldown = (sourceId: string) => {
    setTestCooldowns((current) => ({ ...current, [sourceId]: Date.now() + TEST_COOLDOWN_FALLBACK_MS }));
    setCooldownSeconds(Math.ceil(TEST_COOLDOWN_FALLBACK_MS / 1000));
  };

  const createSource = async () => {
    const name = newName.trim();
    const baseUrl = newBaseUrl.trim();
    const credential = newCredential.trim();
    const authUsername = newEmail.trim();
    if (!name || !baseUrl || !credential) {
      setConnectionMessage(t('portal.importWizard.error.nameRequired'));
      return;
    }
    if (newAuthType === 'cloud_basic' && !authUsername) {
      setConnectionMessage(t('portal.importWizard.error.cloudEmailRequired'));
      return;
    }
    setCreateBusy(true);
    setConnectionMessage(null);
    try {
      const created = await apiJson<ImportSource>('/api/v1/import/sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          base_url: baseUrl,
          auth_type: newAuthType,
          auth_username: newAuthType === 'cloud_basic' ? authUsername : '',
          credential,
        }),
      });
      setSources((current) => [created, ...current]);
      setSelectedSourceId(created.id);
      setCreatingSource(false);
      setTestResult(null);
      setNewName('');
      setNewBaseUrl('');
      setNewEmail('');
      setNewCredential('');
      setConnectionMessage(t('portal.importWizard.sourceCreated'));
    } catch (error) {
      setConnectionMessage(error instanceof ApiError ? error.detail : t('portal.importWizard.error.createSourceFailed'));
    } finally {
      setCreateBusy(false);
    }
  };

  const testConnection = async () => {
    if (!selectedSourceId) return;
    setTestBusy(true);
    setTestResult(null);
    setConnectionMessage(null);
    try {
      const result = await apiJson<ImportSourceTestResponse>(`/api/v1/import/sources/${selectedSourceId}/test`, {
        method: 'POST',
      });
      setTestResult(result);
      armTestCooldown(selectedSourceId);
      if (result.ok && result.server_kind) {
        setSources((current) =>
          current.map((entry) =>
            entry.id === selectedSourceId
              ? { ...entry, server_kind: result.server_kind ?? entry.server_kind, last_validated_at: new Date().toISOString() }
              : entry,
          ),
        );
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        // The server enforces a per-source cooldown but sends no Retry-After;
        // fall back to the documented default window.
        armTestCooldown(selectedSourceId);
        setConnectionMessage(error.detail);
      } else {
        setConnectionMessage(error instanceof ApiError ? error.detail : t('portal.importWizard.error.testFailed'));
      }
    } finally {
      setTestBusy(false);
    }
  };

  const startImport = async (runAfterSave = false) => {
    if (!selectedSourceId) {
      setStartError(t('portal.importWizard.error.pickConnectionFirst'));
      setWizardStep(1);
      return;
    }
    if (!scopeValue.trim()) {
      setStartError(scopeType === 'space' ? t('portal.importWizard.error.enterSpaceKey') : t('portal.importWizard.error.enterPageUrl'));
      setWizardStep(2);
      return;
    }
    const parsedMaxPages = maxPages.trim() ? Number.parseInt(maxPages, 10) : null;
    if (parsedMaxPages !== null && (!Number.isFinite(parsedMaxPages) || parsedMaxPages < 1)) {
      setStartError(t('portal.importWizard.error.maxPagesInvalid'));
      return;
    }
    const parsedMaxDepth = maxDepth.trim() ? Number.parseInt(maxDepth, 10) : null;
    if (parsedMaxDepth !== null && (!Number.isFinite(parsedMaxDepth) || parsedMaxDepth < 0)) {
      setStartError(t('portal.importWizard.error.maxDepthInvalid'));
      return;
    }
    if (!collectionId && !editingSync) {
      setStartError(t('portal.importWizard.error.chooseSpace'));
      return;
    }

    const options = {
      ...(collectionId ? { collection_id: collectionId } : {}),
      max_pages: parsedMaxPages,
      max_depth: parsedMaxDepth,
      include_attachments: includeAttachments,
      ocr_attachments: includeAttachments && ocrAttachments,
      path_tags: pathTags,
      ocr_profile_id: includeAttachments && ocrAttachments && selectedProfileId ? selectedProfileId : null,
      tags: tags
        .split(',')
        .map((entry) => entry.trim())
        .filter(Boolean),
      email: email.trim(),
      webhook_connection_id: webhookConnectionId || null,
    };
    const schedule = { enabled: syncEnabled, interval_seconds: syncInterval };

    setStartBusy(true);
    setStartError(null);
    try {
      if (editingSync) {
        await apiJson<ImportSync>(`/api/v1/import/syncs/${editingSync.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...schedule, options }),
        });
        if (runAfterSave) {
          const run = await apiJson<ImportRun>(`/api/v1/import/syncs/${editingSync.id}/run`, { method: 'POST' });
          router.push(`/imports/${run.id}`);
        } else {
          router.push('/connections?tab=confluence');
        }
        return;
      }
      const run = await apiJson<ImportRun>('/api/v1/import/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_id: selectedSourceId,
          scope: { type: scopeType, value: scopeValue.trim() },
          options,
          sync: schedule,
        }),
      });
      router.push(`/imports/${run.id}`);
    } catch (error) {
      if (error instanceof ApiError) {
        // 409: untested source or an already-active run; 422: bad scope
        // value; 429: rate limited — the backend detail explains all three.
        setStartError(error.detail);
      } else {
        setStartError(t('portal.importWizard.error.startFailed'));
      }
      setStartBusy(false);
    }
  };

  // Toggling the auth scheme clears the typed secret: the field is masked, so
  // a token entered for one scheme must not silently become the credential of
  // the other.
  const selectAuthType = (authType: ImportAuthType) => {
    if (authType !== newAuthType) {
      setNewCredential('');
    }
    setNewAuthType(authType);
  };

  const testDisabled = !selectedSourceId || testBusy || cooldownSeconds > 0 || !sourcesLoaded;

  return (
    <PortalPage
      title={editingSync ? t('portal.importWizard.editTitle') : t('portal.importWizard.title')}
      description={editingSync ? t('portal.importWizard.editSubtitle') : t('portal.importWizard.subtitle')}
    >
      <BackLink fallbackHref="/imports" fallbackLabel={t('portal.importWizard.backToImports')} />
        {prefillNotice && (
          <p className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {prefillNotice}
          </p>
        )}

        <section className="rounded-xl border border-slate-200 bg-gradient-to-br from-emerald-50 to-white p-5">
          <div className="mb-5 grid gap-3 md:grid-cols-3">
            {wizardSteps(t).map((step) => {
              const active = wizardStep === step.id;
              const completed = wizardStep > step.id;
              return (
                <button
                  key={step.id}
                  type="button"
                  onClick={() => {
                    if (step.id < wizardStep && !editingSync) setWizardStep(step.id);
                  }}
                  disabled={Boolean(editingSync) && step.id !== wizardStep}
                  className={`rounded-lg border px-4 py-3 text-left transition ${
                    active
                      ? 'border-emerald-400 bg-emerald-50'
                      : completed
                        ? 'border-slate-200 bg-slate-50'
                        : 'border-slate-200 bg-white'
                  }`}
                >
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-950">
                    <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-xs text-emerald-800">
                      {step.id}
                    </span>
                    {step.title}
                  </div>
                  <p className="mt-2 text-xs text-slate-600">{step.description}</p>
                </button>
              );
            })}
          </div>

          <AnimatePresence mode="wait">
            {wizardStep === 1 && (
              <motion.div
                key="step-1"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -12 }}
                className="space-y-4"
              >
                {!sourcesLoaded ? (
                  <div className="flex items-center gap-2 py-6 text-sm text-slate-600">
                    <LoaderCircle className="h-4 w-4 animate-spin" /> {t('portal.importWizard.loadingConnections')}
                  </div>
                ) : (
                  <>
                    {sources.length > 0 && (
                      <div className="grid gap-3 md:grid-cols-2">
                        {sources.map((source) => {
                          const active = !creatingSource && source.id === selectedSourceId;
                          return (
                            <button
                              key={source.id}
                              type="button"
                              onClick={() => {
                                setSelectedSourceId(source.id);
                                setCreatingSource(false);
                                setTestResult(null);
                                setConnectionMessage(null);
                              }}
                              className={`rounded-xl border p-4 text-left ${
                                active ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
                              }`}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <p className="truncate text-sm font-semibold text-slate-950">{source.name}</p>
                                <span
                                  className={`shrink-0 rounded px-2 py-0.5 text-xs ${
                                    source.server_kind ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'
                                  }`}
                                >
                                  {source.server_kind === 'cloud'
                                    ? t('portal.importWizard.badge.cloud')
                                    : source.server_kind === 'datacenter'
                                      ? t('portal.importWizard.badge.serverDc')
                                      : t('portal.importWizard.badge.untested')}
                                </span>
                              </div>
                              <p className="mt-1 truncate text-xs text-slate-600">{source.base_url}</p>
                              <p className="mt-1 text-xs text-slate-500">
                                {source.auth_type === 'cloud_basic'
                                  ? t('portal.importWizard.authSummary.cloud', { email: source.auth_username })
                                  : t('portal.importWizard.authSummary.pat')}
                              </p>
                            </button>
                          );
                        })}
                        <button
                          type="button"
                          onClick={() => {
                            setCreatingSource(true);
                            setTestResult(null);
                            setConnectionMessage(null);
                          }}
                          className={`rounded-xl border border-dashed p-4 text-left ${
                            creatingSource ? 'border-emerald-300 bg-emerald-50' : 'border-slate-300 bg-white'
                          }`}
                        >
                          <p className="text-sm font-semibold text-slate-950">{t('portal.importWizard.newConnection')}</p>
                          <p className="mt-1 text-xs text-slate-600">{t('portal.importWizard.newConnectionHint')}</p>
                        </button>
                      </div>
                    )}

                    {creatingSource && (
                      <div className="rounded-xl border border-slate-200 bg-white p-4">
                        <p className="text-sm font-semibold text-slate-950">{t('portal.importWizard.createConnection')}</p>
                        <div className="mt-3 grid gap-3 md:grid-cols-2">
                          <label className="text-sm text-slate-600">
                            {t('portal.importWizard.nameLabel')}
                            <input
                              value={newName}
                              onChange={(event) => setNewName(event.target.value)}
                              className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                              placeholder="ACME Confluence"
                            />
                          </label>
                          <label className="text-sm text-slate-600">
                            {t('portal.importWizard.baseUrlLabel')}
                            <input
                              value={newBaseUrl}
                              onChange={(event) => setNewBaseUrl(event.target.value)}
                              className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                              placeholder={newAuthType === 'cloud_basic' ? 'https://acme.atlassian.net' : 'https://confluence.example.com'}
                            />
                          </label>
                          <div className="md:col-span-2">
                            <p className="text-sm text-slate-600">{t('portal.importWizard.authenticationLabel')}</p>
                            <div className="mt-1 grid gap-3 md:grid-cols-2">
                              <button
                                type="button"
                                onClick={() => selectAuthType('pat_bearer')}
                                className={`rounded-xl border p-3 text-left ${
                                  newAuthType === 'pat_bearer' ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
                                }`}
                              >
                                <p className="text-sm font-semibold text-slate-950">{t('portal.importWizard.authServerTitle')}</p>
                                <p className="mt-1 text-xs text-slate-600">{t('portal.importWizard.authServerHint')}</p>
                              </button>
                              <button
                                type="button"
                                onClick={() => selectAuthType('cloud_basic')}
                                className={`rounded-xl border p-3 text-left ${
                                  newAuthType === 'cloud_basic' ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
                                }`}
                              >
                                <p className="text-sm font-semibold text-slate-950">{t('portal.importWizard.authCloudTitle')}</p>
                                <p className="mt-1 text-xs text-slate-600">{t('portal.importWizard.authCloudHint')}</p>
                              </button>
                            </div>
                          </div>
                          {newAuthType === 'cloud_basic' ? (
                            <>
                              <label className="text-sm text-slate-600">
                                {t('portal.importWizard.atlassianEmailLabel')}
                                <input
                                  value={newEmail}
                                  onChange={(event) => setNewEmail(event.target.value)}
                                  type="email"
                                  className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                                  placeholder="name@company.com"
                                />
                              </label>
                              <label className="text-sm text-slate-600">
                                {t('portal.importWizard.apiTokenLabel')}
                                <input
                                  value={newCredential}
                                  onChange={(event) => setNewCredential(event.target.value)}
                                  type="password"
                                  // Browsers ignore "off" on password fields; "new-password"
                                  // (plus the manager hints) suppresses the save prompt for
                                  // this write-only credential.
                                  autoComplete="new-password"
                                  data-1p-ignore
                                  data-lpignore="true"
                                  className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                                  placeholder="Atlassian API token"
                                />
                              </label>
                            </>
                          ) : (
                            <label className="text-sm text-slate-600 md:col-span-2">
                              {t('portal.importWizard.patLabel')}
                              <input
                                value={newCredential}
                                onChange={(event) => setNewCredential(event.target.value)}
                                type="password"
                                autoComplete="new-password"
                                data-1p-ignore
                                data-lpignore="true"
                                className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                                placeholder="Confluence PAT"
                              />
                            </label>
                          )}
                        </div>
                        <p className="mt-2 text-xs text-slate-500">
                          {t('portal.importWizard.tokensEncryptedHint')}
                        </p>
                        <div className="mt-3">
                          <Button onClick={() => void createSource()} disabled={createBusy}>
                            {createBusy ? t('portal.importWizard.creating') : t('portal.importWizard.createConnection')}
                          </Button>
                        </div>
                      </div>
                    )}

                    {!creatingSource && selectedSource && (
                      <div className="flex flex-wrap items-center gap-3">
                        <Button variant="outline" onClick={() => void testConnection()} disabled={testDisabled}>
                          {testBusy
                            ? t('portal.importWizard.testing')
                            : cooldownSeconds > 0
                              ? t('portal.importWizard.testConnectionCooldown', { seconds: cooldownSeconds })
                              : t('portal.importWizard.testConnection')}
                        </Button>
                        {testResult && (
                          <span
                            className={`inline-flex items-center gap-1.5 text-sm ${
                              testResult.ok ? 'text-emerald-700' : 'text-red-600'
                            }`}
                          >
                            {testResult.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                            {testResult.detail ?? (testResult.ok ? t('portal.importWizard.connected') : t('portal.importWizard.connectionFailed'))}
                          </span>
                        )}
                      </div>
                    )}

                    {connectionMessage && (
                      <p aria-live="polite" className="text-sm text-slate-600">
                        {connectionMessage}
                      </p>
                    )}
                    {sourcesLoadFailed && (
                      <div>
                        <Button variant="outline" onClick={retryLoadSources}>
                          {t('portal.importWizard.retryLoadConnections')}
                        </Button>
                      </div>
                    )}
                    {!creatingSource && selectedSource && !selectedSource.server_kind && !(testResult?.ok ?? false) && (
                      <p className="text-xs text-amber-700">
                        {t('portal.importWizard.untestedWarning')}
                      </p>
                    )}

                    <div className="flex justify-end">
                      <Button onClick={() => setWizardStep(2)} disabled={!selectedSourceId || creatingSource}>
                        {t('portal.importWizard.continue')}
                      </Button>
                    </div>
                  </>
                )}
              </motion.div>
            )}

            {wizardStep === 2 && (
              <motion.div
                key="step-2"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -12 }}
                className="space-y-4"
              >
                <div className="grid gap-3 md:grid-cols-2">
                  <button
                    type="button"
                    onClick={() => setScopeType('space')}
                    className={`rounded-xl border p-4 text-left ${
                      scopeType === 'space' ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
                    }`}
                  >
                    <p className="text-sm font-semibold text-slate-950">{t('portal.importWizard.wholeSpaceTitle')}</p>
                    <p className="mt-1 text-xs text-slate-600">{t('portal.importWizard.wholeSpaceHint')}</p>
                  </button>
                  <button
                    type="button"
                    onClick={() => setScopeType('page')}
                    className={`rounded-xl border p-4 text-left ${
                      scopeType === 'page' ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
                    }`}
                  >
                    <p className="text-sm font-semibold text-slate-950">{t('portal.importWizard.pageTreeTitle')}</p>
                    <p className="mt-1 text-xs text-slate-600">{t('portal.importWizard.pageTreeHint')}</p>
                  </button>
                </div>
                <label className="block text-sm text-slate-600">
                  {scopeType === 'space' ? t('portal.importWizard.spaceKeyLabel') : t('portal.importWizard.pageUrlLabel')}
                  <input
                    value={scopeValue}
                    onChange={(event) => setScopeValue(event.target.value)}
                    className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                    placeholder={
                      scopeType === 'space'
                        ? 'DOCS or https://acme.atlassian.net/wiki/spaces/DOCS/...'
                        : '123456 or https://acme.atlassian.net/wiki/spaces/DOCS/pages/123456/Title'
                    }
                  />
                </label>
                <p className="text-xs text-slate-500">
                  {scopeType === 'space' ? t('portal.importWizard.pasteUrlHintSpace') : t('portal.importWizard.pasteUrlHintPage')}
                </p>
                <div className="flex justify-between gap-3">
                  <Button variant="outline" onClick={() => setWizardStep(1)}>
                    {t('common.back')}
                  </Button>
                  <Button onClick={() => setWizardStep(3)} disabled={!scopeValue.trim()}>
                    {t('portal.importWizard.continue')}
                  </Button>
                </div>
              </motion.div>
            )}

            {wizardStep === 3 && (
              <motion.div
                key="step-3"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -12 }}
                className="space-y-4"
              >
                <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-4">
                  <label className="block text-sm text-slate-600">
                    {t('portal.importWizard.spaceLabel')}
                    {editingSync ? (
                      <p className="mt-1 font-medium text-slate-950">
                        {editingSync.collection_name ?? t('portal.connections.confluence.syncNoTarget')}
                      </p>
                    ) : (
                      <select
                        required
                        value={collectionId ?? ''}
                        onChange={(event) => setCollectionId(event.target.value || null)}
                        className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950 md:w-1/2"
                      >
                        <option value="">{t('portal.sourceForm.pleaseSelect')}</option>
                        {spaces.map((space) => (
                          <option key={space.collection_id} value={space.collection_id}>
                            {space.name}
                          </option>
                        ))}
                      </select>
                    )}
                  </label>
                  {editingSync ? (
                    <p className="text-xs text-slate-500">
                      {t('portal.importWizard.fixedScopeHint')}{' '}
                      <Link
                        href={`/imports/new?source=${encodeURIComponent(editingSync.source_id)}`}
                        className="text-emerald-700 hover:text-emerald-800"
                      >
                        {t('portal.importWizard.newImportLink')}
                      </Link>
                    </p>
                  ) : (
                    spaces.length === 0 && (
                      <p className="text-xs text-slate-500">
                        {t('portal.sourceForm.emptySpacesBody')}{' '}
                        <Link href="/knowledge/new" className="text-emerald-700 hover:text-emerald-800">
                          {t('portal.sourceForm.newSpaceLink')}
                        </Link>
                      </p>
                    )
                  )}
                </div>

                <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <Toggle checked={syncEnabled} onChange={setSyncEnabled} label={t('portal.importWizard.syncLabel')} />
                    <select
                      value={syncInterval}
                      onChange={(event) => setSyncInterval(Number(event.target.value))}
                      disabled={!syncEnabled}
                      aria-label={t('portal.connections.confluence.syncIntervalAria')}
                      className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-sm text-slate-950 disabled:opacity-50"
                    >
                      {!intervalOptions.some((option) => option.value === syncInterval) && (
                        <option value={syncInterval}>{formatRefreshInterval(syncInterval, locale)}</option>
                      )}
                      {intervalOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <p className="text-xs text-slate-500">{t('portal.importWizard.syncHint')}</p>
                </div>

                <div className="grid gap-3 md:grid-cols-2">
                  <label className="text-sm text-slate-600">
                    {t('portal.importWizard.maxPagesLabel')}
                    <input
                      value={maxPages}
                      onChange={(event) => setMaxPages(event.target.value)}
                      type="number"
                      min={1}
                      className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                      placeholder={`${SERVER_DEFAULT_MAX_PAGES} (server default)`}
                    />
                    <p className="mt-1 text-xs text-slate-500">{t('portal.importWizard.maxPagesHint')}</p>
                  </label>
                  <label className="text-sm text-slate-600">
                    {t('portal.importWizard.maxDepthLabel')}
                    <input
                      value={maxDepth}
                      onChange={(event) => setMaxDepth(event.target.value)}
                      type="number"
                      min={0}
                      className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                      placeholder={`${SERVER_DEFAULT_MAX_DEPTH} (server default)`}
                    />
                    <p className="mt-1 text-xs text-slate-500">{t('portal.importWizard.maxDepthHint')}</p>
                  </label>
                </div>

                <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-4">
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={includeAttachments}
                      onChange={(event) => setIncludeAttachments(event.target.checked)}
                      className="h-4 w-4 accent-emerald-600"
                    />
                    {t('portal.importWizard.includeAttachments')}
                  </label>
                  <label
                    className={`flex items-center gap-2 text-sm ${includeAttachments ? 'text-slate-700' : 'text-slate-400'}`}
                  >
                    <input
                      type="checkbox"
                      checked={includeAttachments && ocrAttachments}
                      disabled={!includeAttachments}
                      onChange={(event) => setOcrAttachments(event.target.checked)}
                      className="h-4 w-4 accent-emerald-600"
                    />
                    {t('portal.importWizard.ocrAttachments')}
                  </label>
                  {includeAttachments && ocrAttachments && (
                    <label className="block text-sm text-slate-600">
                      {t('portal.importWizard.ocrProfileLabel')}
                      <select
                        value={selectedProfileId}
                        onChange={(event) => setSelectedProfileId(event.target.value)}
                        className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                      >
                        {capabilities.profiles.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      <p className="mt-1 text-xs text-slate-500">
                        {capabilities.profiles.find((option) => option.value === selectedProfileId)?.description ??
                          t('portal.importWizard.ocrProfileFallback')}
                      </p>
                    </label>
                  )}
                  <div>
                    <Toggle checked={pathTags} onChange={setPathTags} label={t('portal.importWizard.hierarchyTagsLabel')} />
                    <p className="mt-1.5 text-xs text-slate-500">
                      {t('portal.importWizard.hierarchyTagsHint')}
                    </p>
                  </div>
                </div>

                <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-4">
                  <p className="text-sm font-semibold text-slate-950">{t('portal.importWizard.webhookTitle')}</p>
                  <p className="text-xs text-slate-600">
                    {t('portal.importWizard.webhookHint')}
                  </p>
                  {webhookConnections.length > 0 ? (
                    <select
                      value={webhookConnectionId}
                      onChange={(event) => setWebhookConnectionId(event.target.value)}
                      className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950 md:w-1/2"
                    >
                      <option value="">{t('portal.importWizard.webhookNone')}</option>
                      {webhookConnections.map((connection) => (
                        <option key={connection.id} value={connection.id}>
                          {connection.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <p className="text-xs text-slate-500">
                      {t('portal.importWizard.noWebhooksPrefix')}{' '}
                      <Link href="/connections?tab=webhooks" className="text-emerald-700 hover:text-emerald-800">
                        {t('portal.importWizard.noWebhooksLink')}
                      </Link>
                      .
                    </p>
                  )}
                </div>

                <div className="grid gap-3 md:grid-cols-2">
                  <label className="text-sm text-slate-600 md:col-span-2">
                    {t('portal.importWizard.tagsLabel')}
                    <input
                      value={tags}
                      onChange={(event) => setTags(event.target.value)}
                      className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                      placeholder="wiki, confluence, docs"
                    />
                  </label>
                  <label className="text-sm text-slate-600 md:col-span-2">
                    {t('portal.importWizard.emailLabel')}
                    <input
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      type="email"
                      className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                      placeholder="name@company.com"
                    />
                  </label>
                </div>

                {startError && (
                  <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                    {startError}
                  </p>
                )}

                <div className="flex flex-wrap justify-between gap-3">
                  {editingSync ? (
                    <Link href="/connections?tab=confluence" className="self-center text-sm text-emerald-700 hover:text-emerald-800">
                      {t('common.cancel')}
                    </Link>
                  ) : (
                    <Button variant="outline" onClick={() => setWizardStep(2)}>
                      {t('common.back')}
                    </Button>
                  )}
                  {editingSync ? (
                    <div className="flex flex-wrap gap-2">
                      <Button variant="outline" onClick={() => void startImport(false)} disabled={startBusy}>
                        {t('portal.importWizard.save')}
                      </Button>
                      <Button onClick={() => void startImport(true)} disabled={startBusy}>
                        {startBusy ? t('common.starting') : t('portal.importWizard.saveAndSync')}
                      </Button>
                    </div>
                  ) : (
                    <Button onClick={() => void startImport()} disabled={startBusy}>
                      {startBusy ? t('common.starting') : t('portal.importWizard.startImport')}
                    </Button>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </section>
    </PortalPage>
  );
}
