'use client';

import { useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Check, FileText, Plus, Sparkles, UploadCloud, X } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { ErrorNotice, Field, Modal, inputClass } from '@/components/admin/admin-shared';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api';
import { peekCached, useCachedResource } from '@/lib/data-cache';
import { listWebhookConnections, type WebhookConnection } from '@/lib/webhooks';
import { useI18n } from '@/i18n/provider';
import { translate } from '@/i18n/messages';
import { DEFAULT_LOCALE, type Locale } from '@/i18n/config';
import {
  API,
  type DuplicateUploadBody,
  type FolderOptions,
  type Job,
  type PaddleCapabilities,
  type PaddleSettings,
  type UploadMode,
  type UploadProgress,
  UploadError,
  buildFolderOptions,
  formatBytes,
  sendFormDataWithProgress,
} from './shared';

/** Existing version number from a 409 duplicate-upload response, if present. */
function duplicateUploadVersion(error: UploadError): number | null {
  const body = error.body as Partial<DuplicateUploadBody> | null;
  return typeof body?.existing_version === 'number' ? body.existing_version : null;
}

/** Friendly message for a 409 duplicate-upload response (falls back to the raw detail). */
function duplicateUploadMessage(fileName: string, error: UploadError, locale: Locale = DEFAULT_LOCALE): string {
  const version = duplicateUploadVersion(error);
  if (version === null) {
    return error.message;
  }
  return translate(locale, 'portal.processingFlow.duplicateFile', { fileName, version });
}

const JOBS_KEY = '/api/v1/jobs';
const PADDLE_SETTINGS_KEY = '/api/v1/paddle/settings';
const PADDLE_CAPABILITIES_KEY = '/api/v1/paddle/capabilities';
const WEBHOOK_CONNECTIONS_KEY = '/api/v1/webhooks/connections';

type WizardStepId = 1 | 2 | 3 | 4;

/** Tone drives the color/role of a flow message; `step` scopes it to render inline
 * at that step instead of (only) in the global fallback banner at the bottom. */
type FlowMessageTone = 'error' | 'warning' | 'info';
type FlowMessage = { text: string; step?: WizardStepId; tone: FlowMessageTone };

export function ProcessingFlow() {
  const { t, locale } = useI18n();
  const router = useRouter();

  const [busy, setBusy] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [wizardStep, setWizardStep] = useState<WizardStepId>(1);
  const [furthestStep, setFurthestStep] = useState<WizardStepId>(1);
  const [mode, setMode] = useState<UploadMode>('single');

  const [department, setDepartment] = useState('');
  const [folder, setFolder] = useState('');
  const [subfolder, setSubfolder] = useState('');
  const [folderOptions, setFolderOptions] = useState<FolderOptions>({});
  const [newFolderName, setNewFolderName] = useState('');
  const [newSubfolderName, setNewSubfolderName] = useState('');
  const [folderBusy, setFolderBusy] = useState(false);
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [folderModalError, setFolderModalError] = useState<string | null>(null);

  const [flowMessage, setFlowMessage] = useState<FlowMessage | null>(null);
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [collectionFiles, setCollectionFiles] = useState<string[]>([]);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);

  // Single-mode: the chosen file is only held in state at step 3 — the
  // actual upload (uploadSingle) fires from the Review & Start button.
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  // Jobs (for the folder picker), the default profile setting, and the
  // profile catalog itself all come from the shared cache: they rarely
  // change between visits, so a remount of this step (e.g. Home ->
  // Processing -> Jobs -> Processing) paints instantly from the last
  // known value instead of showing "Loading profiles..." every time.
  const fetchJobsForFolders = async () => {
    const response = await apiFetch(`/api/v1/jobs`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Failed to load jobs');
    const payload = await response.json();
    return (payload.items ?? []) as Job[];
  };
  const fetchPaddleSettings = async () => {
    const response = await apiFetch(`/api/v1/paddle/settings`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Failed to load paddle settings');
    const payload = await response.json();
    return { default_profile: payload.default_profile, timeout_seconds: payload.timeout_seconds } as PaddleSettings;
  };
  const fetchPaddleCapabilities = async () => {
    const response = await apiFetch(`/api/v1/paddle/capabilities`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Failed to load paddle capabilities');
    const payload = await response.json();
    return { profiles: payload.profiles ?? [] } as PaddleCapabilities;
  };
  // Only enabled connections can actually receive a delivery, so the picker
  // never offers a disabled one -- same filter the backend applies when
  // deciding whether to fire.
  const fetchEnabledWebhookConnections = async () => {
    const payload = await listWebhookConnections();
    return payload.items.filter((connection) => connection.enabled);
  };

  const jobsResource = useCachedResource(JOBS_KEY, fetchJobsForFolders, { ttlMs: 15_000 });
  const settingsResource = useCachedResource(PADDLE_SETTINGS_KEY, fetchPaddleSettings, { ttlMs: 60_000 });
  const capabilitiesResource = useCachedResource(PADDLE_CAPABILITIES_KEY, fetchPaddleCapabilities, {
    ttlMs: 60_000,
  });
  const webhookConnectionsResource = useCachedResource(WEBHOOK_CONNECTIONS_KEY, fetchEnabledWebhookConnections, {
    ttlMs: 60_000,
  });

  const capabilities = capabilitiesResource.data ?? { profiles: [] };
  const enabledWebhookConnections: WebhookConnection[] = webhookConnectionsResource.data ?? [];
  const [selectedProfileId, setSelectedProfileId] = useState(
    () => peekCached<PaddleSettings>(PADDLE_SETTINGS_KEY)?.default_profile ?? 'ppocrv6_tiny',
  );
  const singleFileInputRef = useRef<HTMLInputElement>(null);
  const collectionFileInputRef = useRef<HTMLInputElement>(null);

  // '' = no webhook target; step 2's picker is entirely optional, so this
  // never factors into isStep2Valid below.
  const [webhookConnectionId, setWebhookConnectionId] = useState('');

  const selectedProfile =
    capabilities.profiles.find((option) => option.value === selectedProfileId) ?? capabilities.profiles[0];
  const selectedWebhookConnection = enabledWebhookConnections.find(
    (connection) => connection.id === webhookConnectionId,
  );
  const selectedSubfolderOptions = folder ? (folderOptions[folder] ?? []) : [];

  const refreshFolderOptions = async () => {
    await jobsResource.revalidate();
  };

  // Adopt the cached/fetched settings and jobs snapshots into local state as
  // they change ("adjusting state during render" — see
  // https://react.dev/learn/you-might-not-need-an-effect — rather than a
  // useEffect mirror): the deployment default profile seeds selectedProfileId
  // (which the user can still override in step 2), and jobs feed
  // folderOptions' merge of server folders with locally-created ones.
  const [lastSettingsData, setLastSettingsData] = useState(settingsResource.data);
  if (settingsResource.data !== lastSettingsData) {
    setLastSettingsData(settingsResource.data);
    if (settingsResource.data) {
      setSelectedProfileId(settingsResource.data.default_profile ?? 'ppocrv6_tiny');
    }
  }

  const [lastJobsData, setLastJobsData] = useState(jobsResource.data);
  if (jobsResource.data !== lastJobsData) {
    setLastJobsData(jobsResource.data);
    if (jobsResource.data) {
      setFolderOptions((prev) => buildFolderOptions(prev, jobsResource.data as Job[]));
    }
  }

  const uploadSingle = async (file: File) => {
    if (!selectedProfile) {
      setFlowMessage({ text: t('portal.processingFlow.noProfileYet'), step: 4, tone: 'error' });
      return;
    }
    setBusy(true);
    setFlowMessage(null);
    setUploadProgress({
      phase: 'single',
      currentFile: file.name,
      filesCompleted: 0,
      filesTotal: 1,
      bytesLoaded: 0,
      bytesTotal: file.size || 1,
    });
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('profile_id', selectedProfile.value);
      formData.append('folder', folder.trim());
      formData.append('subfolder', subfolder.trim());
      formData.append('mode', 'single');
      if (webhookConnectionId) {
        formData.append('webhook_connection_id', webhookConnectionId);
      }
      await sendFormDataWithProgress(`${API}/api/v1/upload`, formData, (loaded, total) => {
        setUploadProgress({
          phase: 'single',
          currentFile: file.name,
          filesCompleted: 0,
          filesTotal: 1,
          bytesLoaded: loaded,
          bytesTotal: total || file.size || 1,
        });
      }, locale);
      setFlowMessage({ text: t('portal.processingFlow.singleUploaded'), step: 4, tone: 'info' });
      await refreshFolderOptions();
      router.push('/jobs');
    } catch (error) {
      if (error instanceof UploadError && error.status === 409) {
        setFlowMessage({ text: duplicateUploadMessage(file.name, error, locale), step: 4, tone: 'warning' });
      } else {
        const detail = error instanceof Error ? error.message : t('portal.processingFlow.singleUploadFailed');
        setFlowMessage({ text: detail, step: 4, tone: 'error' });
      }
    } finally {
      setUploadProgress(null);
      setBusy(false);
    }
  };

  const ensureCollection = async () => {
    if (collectionId) {
      return collectionId;
    }
    const response = await apiFetch(`/api/v1/collections`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        department: department.trim(),
        folder: folder.trim(),
        subfolder: subfolder.trim(),
      }),
    });
    if (!response.ok) {
      throw new Error(t('portal.processingFlow.collectionCreateFailed'));
    }
    const payload = await response.json();
    setCollectionId(payload.collection_id);
    return payload.collection_id as string;
  };

  const uploadCollectionFiles = async (files: FileList | File[]) => {
    if (!files.length) {
      return;
    }
    setBusy(true);
    setFlowMessage(null);
    try {
      const id = await ensureCollection();
      const uploadedNames: string[] = [];
      const skippedDuplicates: { name: string; version: number | null }[] = [];
      const fileList = Array.from(files);
      const totalBytes = fileList.reduce((sum, entry) => sum + (entry.size || 0), 0) || 1;
      let completedBytes = 0;
      for (const [index, file] of fileList.entries()) {
        setUploadProgress({
          phase: 'collection',
          currentFile: file.name,
          filesCompleted: index,
          filesTotal: fileList.length,
          bytesLoaded: completedBytes,
          bytesTotal: totalBytes,
        });
        const formData = new FormData();
        formData.append('file', file);
        formData.append('folder', folder.trim());
        formData.append('subfolder', subfolder.trim());
        try {
          await sendFormDataWithProgress(`${API}/api/v1/collections/${id}/upload`, formData, (loaded, total) => {
            setUploadProgress({
              phase: 'collection',
              currentFile: file.name,
              filesCompleted: index,
              filesTotal: fileList.length,
              bytesLoaded: completedBytes + loaded,
              bytesTotal: totalBytes || total || 1,
            });
          }, locale);
        } catch (error) {
          if (error instanceof UploadError && error.status === 409) {
            skippedDuplicates.push({ name: file.name, version: duplicateUploadVersion(error) });
          } else {
            const detail = error instanceof Error ? error.message : t('portal.processingFlow.uploadFailedGeneric');
            setFlowMessage({ text: t('portal.processingFlow.fileUploadFailed', { name: file.name, detail }), step: 3, tone: 'error' });
          }
          continue;
        }
        uploadedNames.push(file.name);
        completedBytes += file.size || 0;
      }
      if (uploadedNames.length > 0) {
        setCollectionFiles((prev) => [...prev, ...uploadedNames]);
      }
      if (uploadedNames.length > 0 || skippedDuplicates.length > 0) {
        const parts: string[] = [];
        if (uploadedNames.length > 0) {
          parts.push(t('portal.processingFlow.filesUploaded', { count: uploadedNames.length }));
        }
        if (skippedDuplicates.length > 0) {
          const details = skippedDuplicates
            .map(({ name, version }) => (version === null ? name : `${name} (v${version})`))
            .join(', ');
          parts.push(
            t('portal.processingFlow.duplicatesSkipped', { count: skippedDuplicates.length, details }),
          );
        }
        setFlowMessage({
          text: parts.join(' '),
          step: 3,
          tone: skippedDuplicates.length > 0 ? 'warning' : 'info',
        });
      }
      await refreshFolderOptions();
    } finally {
      setUploadProgress(null);
      setBusy(false);
    }
  };

  const startCollection = async () => {
    if (!collectionId || !selectedProfile) {
      setFlowMessage({ text: t('portal.processingFlow.uploadCollectionFirst'), step: 4, tone: 'error' });
      return;
    }
    setBusy(true);
    setFlowMessage(null);
    const response = await apiFetch(`/api/v1/collections/${collectionId}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profile_id: selectedProfile.value,
        ...(webhookConnectionId ? { webhook_connection_id: webhookConnectionId } : {}),
      }),
    });
    if (!response.ok) {
      setFlowMessage({ text: t('portal.processingFlow.collectionStartFailed'), step: 4, tone: 'error' });
      setBusy(false);
      return;
    }
    const payload = await response.json();
    setFlowMessage({ text: t('portal.processingFlow.collectionStarted', { count: payload.started_jobs }), step: 4, tone: 'info' });
    await refreshFolderOptions();
    router.push('/jobs');
    setBusy(false);
  };

  const onDropSingle = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    const file = event.dataTransfer.files?.[0];
    if (file) {
      setSelectedFile(file);
    }
  };

  const onDropCollection = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    const files = event.dataTransfer.files;
    if (files && files.length > 0) {
      await uploadCollectionFiles(files);
    }
  };

  const createFolder = async () => {
    const folderValue = newFolderName.trim();
    const subfolderValue = newSubfolderName.trim();
    if (!folderValue && !subfolderValue) {
      setFolderModalError(t('portal.processingFlow.enterFolderName'));
      return;
    }
    setFolderBusy(true);
    setFolderModalError(null);
    const response = await apiFetch(`/api/v1/folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: folderValue, subfolder: subfolderValue }),
    });
    if (!response.ok) {
      setFolderModalError(t('portal.processingFlow.createFolderFailed'));
      setFolderBusy(false);
      return;
    }
    const payload = await response.json();
    const createdPath = String(payload.path ?? '').split('/').filter(Boolean);
    const createdFolder = createdPath[0] ?? '';
    const createdSubfolder = createdPath.length > 1 ? createdPath.slice(1).join('/') : '';
    if (createdFolder) {
      setFolderOptions((prev) => {
        const next = { ...prev };
        const current = new Set(next[createdFolder] ?? []);
        if (createdSubfolder) current.add(createdSubfolder);
        next[createdFolder] = Array.from(current).sort((a, b) => a.localeCompare(b));
        return next;
      });
      setFolder(createdFolder);
      setSubfolder(createdSubfolder);
    }
    setFolderBusy(false);
    closeFolderModal();
    setFlowMessage({ text: t('portal.processingFlow.folderCreated', { path: payload.path }), step: 1, tone: 'info' });
  };

  const closeFolderModal = () => {
    setFolderModalOpen(false);
    setNewFolderName('');
    setNewSubfolderName('');
    setFolderModalError(null);
  };

  // Per-step validity — drives both the Continue button's disabled state and
  // stepReachable() below, so a step can never be jumped to ahead of its
  // prerequisite being satisfied.
  const isStep1Valid = true;
  const isStep2Valid = Boolean(selectedProfile);
  const isStep3Valid = mode === 'single' ? Boolean(selectedFile) : collectionFiles.length > 0;

  const goToStep = (step: WizardStepId) => {
    setWizardStep(step);
    setFurthestStep((prev) => (step > prev ? step : prev));
  };

  const stepReachable = (step: WizardStepId) => step <= furthestStep && (step !== 4 || isStep3Valid);

  const wizardSteps: { id: WizardStepId; label: string; description: string }[] = [
    { id: 1, label: t('portal.processingFlow.step1.label'), description: t('portal.processingFlow.step1.description') },
    { id: 2, label: t('portal.processingFlow.step2.label'), description: t('portal.processingFlow.step2.description') },
    {
      id: 3,
      label: t('portal.processingFlow.step3.label'),
      description: mode === 'single' ? t('portal.processingFlow.step3.descriptionSingle') : t('portal.processingFlow.step3.descriptionCollection'),
    },
    { id: 4, label: t('portal.processingFlow.step4.label'), description: t('portal.processingFlow.step4.description') },
  ];

  const targetFolderLabel = folder.trim()
    ? `${folder.trim()}${subfolder.trim() ? ` / ${subfolder.trim()}` : ''}`
    : t('portal.processingFlow.noFolderInbox');

  /** Inline, step-scoped rendering of `flowMessage` — used at the step it belongs to so
   * the message sits right next to what caused it, instead of only in the global
   * fallback banner at the bottom (which skips messages already shown inline). */
  const renderStepMessage = (step: WizardStepId) => {
    if (!flowMessage || flowMessage.step !== step) return null;
    const toneClass =
      flowMessage.tone === 'error'
        ? 'text-red-600'
        : flowMessage.tone === 'warning'
          ? 'text-amber-700'
          : 'text-emerald-700';
    return (
      <p role={flowMessage.tone === 'error' ? 'alert' : undefined} className={`text-xs ${toneClass}`}>
        {flowMessage.text}
      </p>
    );
  };

  // Step 3's hint doubles as the "why is Continue disabled" explanation and the inline
  // error slot for upload failures/duplicate skips: a message wins when present, else a
  // dezent placeholder hint shows while the step is invalid (no silent click-swallower).
  const step3Message = flowMessage && flowMessage.step === 3 ? flowMessage : null;
  const step3Hint = step3Message ? (
    <p
      role={step3Message.tone === 'error' ? 'alert' : undefined}
      className={`text-xs ${
        step3Message.tone === 'error'
          ? 'text-red-600'
          : step3Message.tone === 'warning'
            ? 'text-amber-700'
            : 'text-slate-500'
      }`}
    >
      {step3Message.text}
    </p>
  ) : !isStep3Valid ? (
    <p className="text-xs text-slate-500">
      {mode === 'single' ? t('portal.processingFlow.selectFileToContinue') : t('portal.processingFlow.uploadFileToContinue')}
    </p>
  ) : null;

  const step2Hint = !isStep2Valid ? (
    <p className="text-xs text-slate-500">{t('portal.processingFlow.waitingForProfiles')}</p>
  ) : null;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-10 text-slate-950 sm:px-6 lg:px-8">
      <section className="mb-8">
        <h1 className="text-3xl font-semibold">{t('portal.processingFlow.title')}</h1>
        <p className="mt-2 text-sm text-slate-600">{t('portal.processingFlow.subtitle')}</p>
      </section>

      <section id="upload-flow" className="mb-8 rounded-xl border border-slate-200 bg-white p-5">
        <nav aria-label={t('portal.processingFlow.stepsAria')} className="mb-6">
          <ol className="flex items-center">
            {wizardSteps.map((step, index) => {
              const active = wizardStep === step.id;
              const completed = wizardStep > step.id;
              const reachable = stepReachable(step.id);
              return (
                <li key={step.id} className={`flex items-center ${index < wizardSteps.length - 1 ? 'flex-1' : ''}`}>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => reachable && goToStep(step.id)}
                      disabled={!reachable}
                      aria-current={active ? 'step' : undefined}
                      title={step.description}
                      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-500 ${
                        active
                          ? 'border-emerald-500 bg-emerald-500 text-white ring-2 ring-emerald-200 ring-offset-2'
                          : completed
                            ? 'border-emerald-300 bg-emerald-100 text-emerald-800'
                            : reachable
                              ? 'border-slate-300 bg-white text-slate-600 hover:border-emerald-300'
                              : 'border-slate-200 bg-slate-50 text-slate-300'
                      }`}
                    >
                      {completed ? <Check className="h-4 w-4" /> : step.id}
                    </button>
                    <span
                      className={`hidden text-sm sm:inline ${
                        active
                          ? 'font-semibold text-emerald-800'
                          : `font-medium ${reachable ? 'text-slate-600' : 'text-slate-300'}`
                      }`}
                    >
                      {step.label}
                    </span>
                  </div>
                  {index < wizardSteps.length - 1 && (
                    <div className={`mx-3 h-px flex-1 ${completed ? 'bg-emerald-300' : 'bg-slate-200'}`} />
                  )}
                </li>
              );
            })}
          </ol>
        </nav>

        <AnimatePresence mode="wait">
          {wizardStep === 1 && (
            <motion.div
              key="step-1"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              className="space-y-4"
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => setMode('single')}
                  className={`flex h-full flex-col rounded-xl border p-4 text-left transition ${mode === 'single' ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white hover:border-emerald-300 hover:bg-emerald-50'}`}
                >
                  <p className="text-sm font-semibold text-slate-950">{t('portal.processingFlow.singleFile')}</p>
                  <p className="mt-1 text-xs text-slate-600">{t('portal.processingFlow.singleFileHint')}</p>
                </button>
                <button
                  type="button"
                  onClick={() => setMode('collection')}
                  className={`flex h-full flex-col rounded-xl border p-4 text-left transition ${mode === 'collection' ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white hover:border-emerald-300 hover:bg-emerald-50'}`}
                >
                  <p className="text-sm font-semibold text-slate-950">{t('portal.processingFlow.multipleFiles')}</p>
                  <p className="mt-1 text-xs text-slate-600">{t('portal.processingFlow.multipleFilesHint')}</p>
                </button>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                {mode === 'collection' && (
                  <label className="text-sm text-slate-600">
                    {t('portal.processingFlow.departmentLabel')}
                    <input
                      value={department}
                      onChange={(event) => setDepartment(event.target.value)}
                      className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                      placeholder="Finance"
                    />
                  </label>
                )}
                <label className="text-sm text-slate-600">
                  {t('portal.processingFlow.targetFolderLabel')}
                  <select
                    value={folder}
                    onChange={(event) => {
                      const nextFolder = event.target.value;
                      setFolder(nextFolder);
                      if (!nextFolder || !(folderOptions[nextFolder] ?? []).includes(subfolder)) {
                        setSubfolder('');
                      }
                    }}
                    className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                  >
                    <option value="">{t('portal.processingFlow.noFolderInbox')}</option>
                    {Object.keys(folderOptions).sort((left, right) => left.localeCompare(right)).map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                </label>
                <label className="text-sm text-slate-600">
                  {t('portal.processingFlow.targetSubfolderLabel')}
                  <select
                    value={subfolder}
                    onChange={(event) => setSubfolder(event.target.value)}
                    disabled={!folder}
                    className="mt-1 w-full rounded border border-slate-200 bg-slate-50 px-3 py-2 text-slate-950"
                  >
                    <option value="">{t('portal.processingFlow.noSubfolder')}</option>
                    {selectedSubfolderOptions.map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                </label>
                <div className="flex items-end md:col-span-2">
                  <Button type="button" variant="outline" onClick={() => setFolderModalOpen(true)}>
                    <Plus className="h-4 w-4" />
                    {t('portal.processingFlow.addFolder')}
                  </Button>
                </div>
              </div>
              {renderStepMessage(1)}
              <div className="flex justify-end">
                <Button onClick={() => goToStep(2)} disabled={!isStep1Valid}>
                  {t('portal.processingFlow.continue')}
                </Button>
              </div>
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
              <div className="flex items-start gap-3 rounded-lg border border-slate-200 bg-emerald-50 p-4">
                <Sparkles className="mt-1 h-5 w-5 text-slate-600" />
                <div>
                  <p className="font-medium text-slate-950">{t('portal.processingFlow.selectedProfile')}</p>
                  <p className="text-sm text-slate-600">{selectedProfile?.label ?? t('portal.processingFlow.loadingProfiles')}</p>
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                {capabilities.profiles.map((profile) => {
                  const active = profile.value === selectedProfileId;
                  return (
                    <button
                      key={profile.value}
                      type="button"
                      onClick={() => setSelectedProfileId(profile.value)}
                      className={`rounded-xl border p-4 text-left transition ${
                        active ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
                      }`}
                    >
                      <p className="text-sm font-semibold text-slate-950">{profile.label}</p>
                      <p className="mt-1 text-xs text-slate-600">{profile.description}</p>
                    </button>
                  );
                })}
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <p className="text-sm font-semibold text-slate-950">{t('portal.processingFlow.sendToWebhook')}</p>
                <p className="mt-1 text-xs text-slate-600">
                  {t('portal.processingFlow.sendToWebhookHint')}
                </p>
                {enabledWebhookConnections.length > 0 ? (
                  <select
                    value={webhookConnectionId}
                    onChange={(event) => setWebhookConnectionId(event.target.value)}
                    className="mt-3 w-full rounded border border-slate-200 bg-white px-3 py-2 text-slate-950 md:w-1/2"
                  >
                    <option value="">{t('portal.processingFlow.webhookNone')}</option>
                    {enabledWebhookConnections.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="mt-3 text-xs text-slate-500">
                    {t('portal.processingFlow.noWebhooksPrefix')}{' '}
                    <Link href="/connections?tab=webhooks" className="text-emerald-700 hover:text-emerald-800">
                      {t('portal.processingFlow.noWebhooksLink')}
                    </Link>
                  </p>
                )}
              </div>
              {step2Hint}
              <div className="flex justify-between gap-3">
                <Button variant="outline" onClick={() => goToStep(1)}>
                  {t('portal.processingFlow.back')}
                </Button>
                <Button
                  onClick={() => goToStep(3)}
                  disabled={!isStep2Valid}
                  title={isStep2Valid ? undefined : t('portal.processingFlow.waitingForProfilesTitle')}
                >
                  {t('portal.processingFlow.continue')}
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
              {mode === 'single' ? (
                selectedFile ? (
                  <div className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-slate-50 p-5">
                    <div className="flex items-center gap-3">
                      <FileText className="h-8 w-8 text-slate-400" />
                      <div>
                        <p className="text-sm font-semibold text-slate-950">{selectedFile.name}</p>
                        <p className="text-xs text-slate-500">{formatBytes(selectedFile.size)}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button type="button" variant="outline" size="sm" onClick={() => singleFileInputRef.current?.click()}>
                        {t('portal.processingFlow.replace')}
                      </Button>
                      <button
                        type="button"
                        onClick={() => setSelectedFile(null)}
                        aria-label={t('portal.processingFlow.removeFileAria')}
                        className="rounded-full p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <motion.div
                    onDrop={onDropSingle}
                    onDragOver={(event) => event.preventDefault()}
                    onDragEnter={() => setDragActive(true)}
                    onDragLeave={() => setDragActive(false)}
                    className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-10 text-center"
                    animate={{ borderColor: dragActive ? '#6ee7b7' : '#10b981' }}
                  >
                    <UploadCloud className="mx-auto mb-4 h-10 w-10 text-slate-600" />
                    <p className="mb-2 text-lg font-medium">{t('portal.processingFlow.dragDropFile')}</p>
                    <p className="mb-4 text-sm text-slate-600">{t('portal.processingFlow.fileTypesMax')}</p>
                    <p className="mb-4 text-xs text-slate-500">{t('portal.processingFlow.targetFolderInline', { label: targetFolderLabel })}</p>
                    <Button variant="outline" onClick={() => singleFileInputRef.current?.click()}>
                      {t('portal.processingFlow.selectFile')}
                    </Button>
                  </motion.div>
                )
              ) : (
                <>
                  <motion.div
                    onDrop={onDropCollection}
                    onDragOver={(event) => event.preventDefault()}
                    onDragEnter={() => setDragActive(true)}
                    onDragLeave={() => setDragActive(false)}
                    className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-10 text-center"
                    animate={{ borderColor: dragActive ? '#6ee7b7' : '#10b981' }}
                  >
                    <UploadCloud className="mx-auto mb-4 h-10 w-10 text-slate-600" />
                    <p className="mb-2 text-lg font-medium">{t('portal.processingFlow.uploadAllCollectionFiles')}</p>
                    <p className="mb-4 text-xs text-slate-500">{t('portal.processingFlow.targetFolderInline', { label: targetFolderLabel })}</p>
                    <Button variant="outline" onClick={() => collectionFileInputRef.current?.click()}>
                      {t('portal.processingFlow.selectFiles')}
                    </Button>
                  </motion.div>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                    <p className="font-semibold text-slate-950">{t('portal.processingFlow.uploadedFilesCount', { count: collectionFiles.length })}</p>
                    {collectionFiles.length > 0 && (
                      <ul className="mt-2 max-h-28 space-y-1 overflow-y-auto text-xs text-slate-500">
                        {collectionFiles.map((name, index) => (
                          <li key={`${name}-${index}`}>{name}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </>
              )}
              <input
                ref={singleFileInputRef}
                type="file"
                className="hidden"
                accept=".pdf,.docx,.pptx,.xlsx,.xls,.png,.jpg,.jpeg,.eml,message/rfc822"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file) {
                    setSelectedFile(file);
                  }
                  event.currentTarget.value = '';
                }}
              />
              <input
                ref={collectionFileInputRef}
                type="file"
                multiple
                className="hidden"
                accept=".pdf,.docx,.pptx,.xlsx,.xls,.png,.jpg,.jpeg,.eml,message/rfc822"
                onChange={async (event) => {
                  const files = event.currentTarget.files;
                  if (files) {
                    await uploadCollectionFiles(files);
                  }
                  event.currentTarget.value = '';
                }}
              />
              {step3Hint}
              <div className="flex justify-between gap-3">
                <Button variant="outline" onClick={() => goToStep(2)}>
                  {t('portal.processingFlow.back')}
                </Button>
                <Button
                  onClick={() => goToStep(4)}
                  disabled={!isStep3Valid}
                  title={
                    isStep3Valid
                      ? undefined
                      : mode === 'single'
                        ? t('portal.processingFlow.selectFileTitle')
                        : t('portal.processingFlow.uploadFileTitle')
                  }
                >
                  {t('portal.processingFlow.continue')}
                </Button>
              </div>
            </motion.div>
          )}

          {wizardStep === 4 && (
            <motion.div
              key="step-4"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              className="space-y-4"
            >
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-5">
                <p className="mb-3 text-sm font-semibold text-slate-950">{t('portal.processingFlow.reviewHeading')}</p>
                <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{t('portal.processingFlow.modeLabel')}</dt>
                    <dd className="mt-0.5 text-slate-950">{mode === 'single' ? t('portal.processingFlow.singleFile') : t('portal.processingFlow.multipleFiles')}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{t('portal.processingFlow.targetFolderDt')}</dt>
                    <dd className="mt-0.5 text-slate-950">{targetFolderLabel}</dd>
                  </div>
                  {mode === 'collection' && department.trim() && (
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{t('portal.processingFlow.departmentDt')}</dt>
                      <dd className="mt-0.5 text-slate-950">{department.trim()}</dd>
                    </div>
                  )}
                  <div>
                    <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{t('portal.processingFlow.profileDt')}</dt>
                    <dd className="mt-0.5 text-slate-950">{selectedProfile?.label ?? t('portal.processingFlow.noProfileSelected')}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{t('portal.processingFlow.webhookDt')}</dt>
                    <dd className="mt-0.5 text-slate-950">{selectedWebhookConnection?.name ?? t('portal.processingFlow.notConfigured')}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                      {mode === 'single' ? t('portal.processingFlow.fileDt') : t('portal.processingFlow.filesDt')}
                    </dt>
                    <dd className="mt-0.5 text-slate-950">
                      {mode === 'single'
                        ? selectedFile
                          ? `${selectedFile.name} (${formatBytes(selectedFile.size)})`
                          : t('portal.processingFlow.noFileSelected')
                        : t('portal.processingFlow.filesCount', { count: collectionFiles.length })}
                    </dd>
                  </div>
                </dl>
              </div>
              {renderStepMessage(4)}
              <div className="flex justify-between gap-3">
                <Button variant="outline" onClick={() => goToStep(3)}>
                  {t('portal.processingFlow.back')}
                </Button>
                {mode === 'single' ? (
                  <Button
                    onClick={() => selectedFile && uploadSingle(selectedFile)}
                    disabled={!selectedFile || !selectedProfile || busy}
                    title={!selectedFile ? t('portal.processingFlow.selectFileFirstTitle') : !selectedProfile ? t('portal.processingFlow.noProfileSelectedTitle') : undefined}
                  >
                    {t('portal.processingFlow.startProcessing')}
                  </Button>
                ) : (
                  <Button
                    onClick={startCollection}
                    disabled={!collectionId || collectionFiles.length === 0 || busy}
                    title={!collectionId || collectionFiles.length === 0 ? t('portal.processingFlow.uploadCollectionFirst') : undefined}
                  >
                    {t('portal.processingFlow.startCollectionProcessing')}
                  </Button>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Fallback banner: a message already shown inline at its own step (via
            renderStepMessage/step3Hint above) is skipped here to avoid showing it twice.
            It still surfaces here when the user has navigated to a different step than
            the one the message belongs to — e.g. a step-3 upload finishing while the user
            already moved on — so feedback is never silently swallowed. */}
        {flowMessage && flowMessage.step !== wizardStep && (
          <div
            role={flowMessage.tone === 'error' ? 'alert' : undefined}
            aria-live={flowMessage.tone === 'error' ? undefined : 'polite'}
            className={`mt-4 rounded-xl border px-4 py-3 text-sm ${
              flowMessage.tone === 'error'
                ? 'border-red-200 bg-red-50 text-red-700'
                : flowMessage.tone === 'warning'
                  ? 'border-amber-200 bg-amber-50 text-amber-800'
                  : 'border-slate-200 bg-slate-50 text-slate-700'
            }`}
          >
            {flowMessage.text}
          </div>
        )}
        {uploadProgress && (
          <div className="mt-3 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-700" aria-live="polite">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="font-medium text-slate-950">
                  {uploadProgress.phase === 'single' ? t('portal.processingFlow.uploadingFile') : t('portal.processingFlow.uploadingCollectionFiles')}
                </p>
                <p className="text-xs text-slate-500">{uploadProgress.currentFile}</p>
              </div>
              <p className="text-xs font-semibold text-slate-600">
                {t('portal.processingFlow.filesProgress', { completed: uploadProgress.filesCompleted, total: uploadProgress.filesTotal })}
              </p>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-emerald-500 transition-all"
                style={{
                  width: `${Math.min(100, Math.round((uploadProgress.bytesLoaded / uploadProgress.bytesTotal) * 100))}%`,
                }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
              <span>{Math.min(100, Math.round((uploadProgress.bytesLoaded / uploadProgress.bytesTotal) * 100))}%</span>
              <span>
                {formatBytes(uploadProgress.bytesLoaded)} / {formatBytes(uploadProgress.bytesTotal)}
              </span>
            </div>
          </div>
        )}
      </section>

      {folderModalOpen && (
        <Modal title={t('portal.processingFlow.addFolderModalTitle')} onClose={closeFolderModal}>
          <div className="space-y-4">
            <Field label={t('portal.processingFlow.newFolderLabel')}>
              <input
                value={newFolderName}
                onChange={(event) => setNewFolderName(event.target.value)}
                className={inputClass}
                placeholder="invoices"
                autoFocus
              />
            </Field>
            <Field label={t('portal.processingFlow.newSubfolderLabel')}>
              <input
                value={newSubfolderName}
                onChange={(event) => setNewSubfolderName(event.target.value)}
                className={inputClass}
                placeholder="2026/april"
              />
            </Field>
            <ErrorNotice message={folderModalError} />
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" size="sm" onClick={closeFolderModal} disabled={folderBusy}>
                {t('common.cancel')}
              </Button>
              <Button type="button" size="sm" onClick={createFolder} disabled={folderBusy}>
                {folderBusy ? t('portal.processingFlow.adding') : t('portal.processingFlow.createFolder')}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
