'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Bot, Download, KeyRound, MoreHorizontal, Pencil, Plus, Trash2, Upload, Workflow } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiFetch, apiJson } from '@/lib/api';
import type { Team } from '@/lib/auth-types';
import { BotSharing } from '@/components/bot-sharing';
import { BotImportDialog } from './bot-import-dialog';
import { toGrantInputs, type BotGrant } from '@/lib/bots';
import type { KnowledgeSpace } from '@/lib/portal';
import {
  Badge,
  ConfirmDialog,
  ErrorNotice,
  Field,
  LoadingState,
  Modal,
  SectionCard,
  Toggle,
  apiSend,
  errorMessage,
  inputClass,
  useAdminList,
} from './admin-shared';
import { useI18n } from '@/i18n/provider';
import type { MessageKey, MessageVars } from '@/i18n/messages';

// Mirrors Weave-Runtime's `AgentConfig`/`SubagentConfig`/`AgentLimits`
// (services/runtime/backend/app/schemas/bot.py) and Weave-Ingest's own
// pydantic mirror of it (services/ingest/backend/app/schemas/
// managed_bots.py's `AgentConfig`) field-for-field -- the payload this
// editor builds is exactly the `agent` JSON Weave-Runtime's own
// `BotConfig.agent` accepts, so field names/shapes here must stay in sync
// with both of those, never renamed independently.
type AgentSubagentFilters = {
  team?: string | null;
  department?: string | null;
  tags?: string[];
  source?: string | null;
  language?: string | null;
  document_type?: string | null;
};

type AgentSubagentModel = {
  provider: string;
  model: string;
  temperature?: number | null;
  supports_tools?: boolean | null;
};

type AgentSubagentLimits = {
  max_searches: number;
  max_results: number;
  timeout_seconds: number;
};

type AgentSubagent = {
  id: string;
  name: string;
  description?: string | null;
  mission: string;
  collections: string[];
  filters: AgentSubagentFilters;
  include_uncollected: boolean;
  model?: AgentSubagentModel | null;
  endpoint?: string | null;
  limits: AgentSubagentLimits;
};

type AgentLimits = {
  max_parallel: number;
  max_followups: number;
  budget_searches: number;
  timeout_seconds: number;
};

type AgentConfig = {
  enabled: boolean;
  subagents: AgentSubagent[];
  limits: AgentLimits;
};

// Runtime's BotConfig.intake / guard.blocked_topics (bot document shape).
type IntakeConfig = { question: string; options: string[]; allow_free_text: boolean; label: string | null };
type BlockedTopic = { name: string; description: string | null; keywords: string[] };

type ManagedBot = {
  id: string;
  kind: 'n8n' | 'llm';
  name: string;
  description: string | null;
  enabled: boolean;
  webhook_url: string;
  streaming: boolean;
  has_auth_token: boolean;
  timeout_seconds: number;
  public: boolean;
  grants: BotGrant[];
  collections: string[];
  require_sources: boolean;
  no_context_reply: string;
  system_prompt?: string | null;
  temperature?: number | null;
  retrieval_enabled?: boolean;
  retrieval_filters?: Record<string, string | string[] | null>;
  top_k?: number;
  final_k?: number;
  rerank?: boolean;
  include_uncollected?: boolean;
  min_score?: number | null;
  clarify_below?: number | null;
  weak_context_below?: number | null;
  query_rewrite?: boolean | null;
  uncertainty_hint?: boolean;
  agent?: AgentConfig | null;
  // Guard fields without a column of their own (clarify_*), only the set ones.
  config_extras?: Record<string, number | string> | null;
  // Chat presentation: hide the sources panel; a note next to the composer.
  show_sources?: boolean;
  disclaimer?: string | null;
  intake?: IntakeConfig | null;
  blocked_topics?: BlockedTopic[];
  blocked_topic_reply?: string | null;
  llm_endpoint?: string | null;
  llm_endpoints?: string[];
  created_at: string;
  updated_at: string;
  source?: 'managed' | 'runtime';
  editable?: boolean;
  runtime_rejected?: boolean;
};

type BotDraft = Omit<ManagedBot, 'has_auth_token' | 'created_at' | 'updated_at'> & {
  auth_token: string;
  clear_auth_token: boolean;
  has_auth_token: boolean;
};

/** A 0..1 score field: empty input means "use the runtime default" (null), never 0. */
const scoreOrNull = (raw: string): number | null => {
  if (raw.trim() === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : null;
};

const emptyDraft = (noContextReply: string): BotDraft => ({
  id: '',
  kind: 'llm',
  name: '',
  description: null,
  enabled: true,
  webhook_url: '',
  streaming: false,
  auth_token: '',
  clear_auth_token: false,
  has_auth_token: false,
  timeout_seconds: 120,
  public: false,
  grants: [],
  collections: [],
  require_sources: true,
  no_context_reply: noContextReply,
  system_prompt: '',
  temperature: 0.2,
  retrieval_enabled: false,
  retrieval_filters: {},
  top_k: 20,
  final_k: 5,
  rerank: true,
  include_uncollected: false,
  min_score: null,
  clarify_below: null,
  weak_context_below: null,
  query_rewrite: null,
  uncertainty_hint: true,
  show_sources: true,
  disclaimer: null,
  intake: null,
  blocked_topics: [],
  blocked_topic_reply: null,
  agent: null,
  llm_endpoint: null,
  llm_endpoints: [],
});

type LlmEndpointOption = { id: string; name: string; model: string; enabled: boolean; supports_tools?: boolean };

type BotStep = 'basics' | 'behavior' | 'knowledge' | 'agents' | 'sharing' | 'review';

const emptyIntake = (): IntakeConfig => ({ question: '', options: [], allow_free_text: true, label: null });
const cleanList = (values: string[]) => values.map(value => value.trim()).filter(Boolean);

const emptyAgentLimits = (): AgentLimits => ({ max_parallel: 3, max_followups: 1, budget_searches: 9, timeout_seconds: 120 });
const emptyAgent = (): AgentConfig => ({ enabled: true, subagents: [], limits: emptyAgentLimits() });

function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function emptySubagent(existingIds: string[]): AgentSubagent {
  let id = 'subagent';
  let suffix = 1;
  while (existingIds.includes(id)) {
    suffix += 1;
    id = `subagent-${suffix}`;
  }
  return {
    id, name: '', description: null, mission: '', collections: [], filters: {}, include_uncollected: false,
    model: null, limits: { max_searches: 3, max_results: 5, timeout_seconds: 60 },
  };
}

type Translate = (key: MessageKey, vars?: MessageVars) => string;

/** Client-side mirror of Weave-Runtime's own `AgentConfig` validation
 * rules (services/runtime/backend/app/schemas/bot.py's `AgentConfig`/
 * `SubagentConfig`) -- catches a malformed agent-mode configuration in the
 * editor itself, with a localized message, instead of only surfacing it as a
 * 422 from Weave-Ingest's own mirror (schemas/managed_bots.py) after
 * saving already failed. Returns an empty array when `agent` is
 * `null` or disabled, since a disabled/absent agent block has nothing to
 * validate. */
function validateAgent(agent: AgentConfig | null, t: Translate): string[] {
  if (!agent || !agent.enabled) return [];
  const errors: string[] = [];
  if (agent.subagents.length === 0) {
    errors.push(t('admin.bots.error.agentRequired'));
  }
  const ids = agent.subagents.map(subagent => subagent.id);
  const duplicates = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
  if (duplicates.length > 0) {
    errors.push(t('admin.bots.error.duplicateIds', { ids: duplicates.join(', ') }));
  }
  for (const subagent of agent.subagents) {
    const label = subagent.name.trim() || subagent.id || t('admin.bots.unnamedSubagent');
    if (!subagent.id.trim()) errors.push(t('admin.bots.error.emptyId', { label }));
    if (!subagent.name.trim()) errors.push(t('admin.bots.error.emptyName', { label }));
    if (!subagent.mission.trim()) errors.push(t('admin.bots.error.emptyMission', { label }));
    if (subagent.collections.length === 0 && !subagent.include_uncollected) {
      errors.push(t('admin.bots.error.missingScope', { label, toggleLabel: t('admin.bots.toggle.includeUncollected') }));
    }
    if (subagent.model && !subagent.model.model.trim()) {
      errors.push(t('admin.bots.error.emptyModelOverride', { label }));
    }
  }
  return errors;
}

/** Host of the webhook for the tile; the full URL stays in the title. */
function webhookHost(url: string) {
  try { return new URL(url).host; } catch { return url; }
}

/** "More actions" menu of a bot tile: Escape and an outside click close it. */
function BotMenu({ name, onExport, onDelete }: { name: string; onExport: () => void; onDelete?: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => { if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); triggerRef.current?.focus(); } };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onPointer); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const itemClass = 'flex w-full items-center gap-2 px-3 py-2 text-left text-[15px] hover:bg-[var(--hover)] focus-visible:bg-[var(--hover)] focus-visible:outline-none';
  return <div ref={rootRef} className="relative flex-none">
    <Button ref={triggerRef} variant="ghost" size="icon" className="h-8 w-8" aria-label={t('admin.bots.moreAria', { name })} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(value => !value)}><MoreHorizontal size={18} aria-hidden="true" /></Button>
    {open && <div role="menu" className="absolute right-0 z-20 mt-1 min-w-44 overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--surface)] py-1 shadow-lg">
      <button type="button" role="menuitem" className={itemClass} aria-label={t('admin.bots.exportAria', { name })} onClick={() => { setOpen(false); onExport(); }}><Download size={15} aria-hidden="true" />{t('admin.bots.export')}</button>
      {onDelete && <button type="button" role="menuitem" className={`${itemClass} text-red-700`} aria-label={t('admin.bots.deleteAria', { name })} onClick={() => { setOpen(false); onDelete(); }}><Trash2 size={15} aria-hidden="true" />{t('common.delete')}</button>}
    </div>}
  </div>;
}

export function BotsTab() {
  const { t } = useI18n();
  const bots = useAdminList<ManagedBot>('/api/v1/auth/admin/bots');
  const teams = useAdminList<Team>('/api/v1/auth/admin/teams');
  const spaces = useAdminList<KnowledgeSpace>('/api/v1/collections');
  const [editing, setEditing] = useState<ManagedBot | 'new' | null>(null);
  const [deleting, setDeleting] = useState<ManagedBot | null>(null);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState('');
  const [exportError, setExportError] = useState<string | null>(null);

  /** Downloads the bot as a portable bot document (contracts/bot-document.md); also for Runtime's YAML bots. */
  async function exportBot(item: ManagedBot) {
    setExportError(null);
    try {
      const response = await apiFetch(`/api/v1/auth/admin/bots/${encodeURIComponent(item.id)}/export?format=yaml`);
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        throw new ApiError(response.status, typeof body?.detail === 'string' ? body.detail : t('admin.bots.exportFailed'));
      }
      const blob = await response.blob();
      const fileName = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? `${item.id}.bot.yaml`;
      const href = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(href);
    } catch (err) {
      setExportError(errorMessage(err));
    }
  }

  const loading = bots.loading || teams.loading || spaces.loading;
  const error = bots.error || teams.error || spaces.error;

  const [deleteBodyBefore, deleteBodyAfter] = t('admin.bots.delete.body').split('{name}');

  return <div className="space-y-6">
    <SectionCard
      title={t('admin.bots.title')}
      description={t('admin.bots.description')}
      actions={<div className="flex gap-2"><Button variant="outline" onClick={() => { setImporting(true); setNotice(''); }}><Upload size={15} />{t('admin.bots.import')}</Button><Button onClick={() => { setEditing('new'); setNotice(''); }}><Plus size={15} />{t('admin.bots.add')}</Button></div>}
    >
      <ErrorNotice message={error || exportError} />
      {notice && <p role="status" className="mb-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}
      {loading ? <LoadingState label={t('admin.bots.loading')} /> : bots.items.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <Workflow className="h-9 w-9 text-slate-300" />
          <div><p className="font-medium text-slate-800">{t('admin.bots.empty.title')}</p><p className="mt-1 text-sm text-slate-500">{t('admin.bots.empty.body')}</p></div>
          <Button variant="outline" size="sm" onClick={() => setEditing('new')}><Plus size={15} />{t('admin.bots.addFirst')}</Button>
        </div>
      ) : <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {bots.items.map(item => <li key={item.id} className="flex min-w-0 flex-col gap-3 rounded-xl border border-[var(--line)] p-4">
          <div className="flex items-start gap-2">
            <Bot size={18} className="mt-0.5 flex-none text-emerald-700" aria-hidden="true" />
            <h3 className="min-w-0 flex-1 line-clamp-2 text-[15px] font-semibold text-slate-950">{item.name}</h3>
            <BotMenu name={item.name} onExport={() => void exportBot(item)} onDelete={item.editable !== false ? () => setDeleting(item) : undefined} />
          </div>
          <div className="flex flex-wrap items-center gap-2"><Badge tone={item.enabled ? 'emerald' : 'slate'}>{item.enabled ? t('admin.bots.status.active') : t('admin.bots.status.inactive')}</Badge><Badge tone={item.source === 'runtime' ? 'slate' : 'emerald'}>{item.source === 'runtime' ? t('admin.bots.kind.configBot') : t('admin.bots.kind.managed')}</Badge>{item.streaming && <Badge tone="amber">{t('admin.bots.badge.streaming')}</Badge>}{item.has_auth_token && <Badge tone="amber">{t('admin.bots.badge.bearerToken')}</Badge>}{item.runtime_rejected && <Badge tone="red">{t('admin.bots.badge.notInChat')}</Badge>}</div>
          <p className="line-clamp-3 text-[15px] text-slate-600">{item.description || item.id}</p>
          {item.runtime_rejected && <p role="status" className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{item.kind === 'n8n' ? t('admin.bots.runtimeRejected.n8n') : t('admin.bots.runtimeRejected.other')}</p>}
          {item.webhook_url && <p className="truncate text-[13px] text-slate-500" title={item.webhook_url}>{webhookHost(item.webhook_url)}</p>}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
            <div className="min-w-0"><dt className="text-slate-500">{t('admin.bots.meta.teams')}</dt><dd className="font-medium text-slate-800">{item.public ? t('admin.bots.teamsAll') : item.grants.filter(grant => grant.role === 'user').map(grant => grant.name).join(', ') || t('admin.bots.teamsNone')}</dd></div>
            <div className="min-w-0"><dt className="text-slate-500">{t('admin.bots.meta.owners')}</dt><dd className="font-medium text-slate-800">{item.grants.filter(grant => grant.role === 'owner').map(grant => grant.name).join(', ') || '—'}</dd></div>
            <div className="min-w-0"><dt className="text-slate-500">{t('admin.bots.meta.spaces')}</dt><dd className="font-medium text-slate-800">{item.collections.length ? item.collections.length : t('admin.bots.spacesAllAuthorized')}</dd></div>
          </dl>
          {item.editable !== false && <div className="mt-auto flex pt-1"><Button variant="outline" size="sm" onClick={() => setEditing(item)} aria-label={t('admin.bots.editAria', { name: item.name })}><Pencil size={15} />{t('common.edit')}</Button></div>}
        </li>)}
      </ul>}
    </SectionCard>

    {editing && <BotEditor bot={editing === 'new' ? null : editing} teams={teams.items} spaces={spaces.items} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); setNotice(t('admin.bots.saved')); await bots.reload(); }} />}
    {importing && <BotImportDialog onClose={() => setImporting(false)} onImported={async () => { setNotice(t('admin.bots.imported')); await bots.reload(); }} />}
    {deleting && <ConfirmDialog title={t('admin.bots.delete.title')} body={<p>{deleteBodyBefore}<strong className="text-slate-950">{deleting.name}</strong>{deleteBodyAfter}</p>} confirmLabel={t('admin.bots.delete.title')} onClose={() => setDeleting(null)} onConfirm={async () => { await apiSend(`/api/v1/auth/admin/bots/${encodeURIComponent(deleting.id)}`, { method: 'DELETE' }); setDeleting(null); setNotice(t('admin.bots.deleted')); await bots.reload(); }} />}
  </div>;
}

function BotEditor({ bot, teams, spaces, onClose, onSaved }: { bot: ManagedBot | null; teams: Team[]; spaces: KnowledgeSpace[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<BotDraft>(() => bot ? { ...bot, auth_token: '', clear_auth_token: false } : emptyDraft(t('admin.bots.defaultNoContextReply')));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<BotStep>('basics');
  const set = <K extends keyof BotDraft>(key: K, value: BotDraft[K]) => setDraft(current => ({ ...current, [key]: value }));
  // Admin > Chat & LLM endpoints; a failed load only hides the choice.
  const [endpoints, setEndpoints] = useState<LlmEndpointOption[]>([]);
  useEffect(() => {
    let cancelled = false;
    apiJson<{ items: LlmEndpointOption[] }>('/api/v1/auth/admin/chat-provider/endpoints')
      .then(result => { if (!cancelled) setEndpoints(result.items); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);
  const ownEndpointOptions = [
    ...endpoints.filter(endpoint => endpoint.id !== 'default'),
    ...(draft.llm_endpoint && !endpoints.some(endpoint => endpoint.id === draft.llm_endpoint)
      ? [{ id: draft.llm_endpoint, name: draft.llm_endpoint, model: '', enabled: false }] : []),
  ];
  const toggleValue = (key: 'collections', value: string) => set(key, draft[key].includes(value) ? draft[key].filter(item => item !== value) : [...draft[key], value]);
  const agentErrors = draft.kind === 'llm' ? validateAgent(draft.agent ?? null, t) : [];
  const intake = draft.kind === 'llm' ? draft.intake ?? null : null;
  const guardRuleErrors = [
    ...(intake && !intake.question.trim() ? [t('admin.bots.problem.intakeQuestion')] : []),
    ...(intake && !intake.allow_free_text && cleanList(intake.options).length === 0 ? [t('admin.bots.problem.intakeOptions')] : []),
    ...((draft.blocked_topics ?? []).some(topic => !topic.name.trim()) ? [t('admin.bots.problem.blockedTopicName')] : []),
  ];
  const canSave = Boolean(
    draft.id.trim() && draft.name.trim() &&
    (draft.kind === 'llm' ? draft.system_prompt?.trim() : draft.webhook_url?.trim()) &&
    agentErrors.length === 0 && guardRuleErrors.length === 0 && !saving
  );

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true); setError(null);
    const body = {
      kind: draft.kind,
      name: draft.name,
      description: draft.description,
      enabled: draft.enabled,
      webhook_url: draft.kind === 'n8n' ? draft.webhook_url : null,
      system_prompt: draft.kind === 'llm' ? draft.system_prompt : null,
      temperature: draft.kind === 'llm' ? draft.temperature : null,
      retrieval_enabled: draft.kind === 'llm' ? draft.retrieval_enabled : false,
      retrieval_filters: draft.kind === 'llm' ? draft.retrieval_filters : {},
      top_k: draft.top_k,
      final_k: draft.final_k,
      rerank: draft.rerank,
      include_uncollected: draft.include_uncollected,
      streaming: draft.streaming,
      auth_token: draft.auth_token || null,
      clear_auth_token: draft.clear_auth_token,
      timeout_seconds: draft.timeout_seconds,
      public: draft.public,
      grants: toGrantInputs(draft.grants),
      collections: draft.collections,
      require_sources: draft.require_sources,
      no_context_reply: draft.no_context_reply,
      min_score: draft.min_score ?? null,
      clarify_below: draft.clarify_below ?? null,
      weak_context_below: draft.weak_context_below ?? null,
      query_rewrite: draft.query_rewrite ?? null,
      uncertainty_hint: draft.uncertainty_hint ?? true,
      config_extras: draft.config_extras ?? null,
      show_sources: draft.show_sources ?? true,
      disclaimer: draft.disclaimer?.trim() || null,
      intake: intake ? { ...intake, question: intake.question.trim(), options: cleanList(intake.options), label: intake.label?.trim() || null } : null,
      blocked_topics: (draft.blocked_topics ?? []).map(topic => ({ name: topic.name.trim(), description: topic.description?.trim() || null, keywords: cleanList(topic.keywords) })),
      blocked_topic_reply: draft.blocked_topic_reply?.trim() || null,
      agent: draft.kind === 'llm' && draft.agent?.enabled ? draft.agent : null,
      llm_endpoint: draft.kind === 'llm' ? draft.llm_endpoint || null : null,
      llm_endpoints: draft.kind === 'llm' ? draft.llm_endpoints ?? [] : [],
      ...(!bot ? { id: draft.id } : {}),
    };
    try {
      await apiJson(bot ? `/api/v1/auth/admin/bots/${encodeURIComponent(bot.id)}` : '/api/v1/auth/admin/bots', {
        method: bot ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      await onSaved();
    } catch (err) { setError(errorMessage(err)); setSaving(false); }
  }

  const modalTitle = bot
    ? t('admin.bots.editTitle', { name: bot.name })
    : (draft.kind === 'n8n' ? t('admin.bots.add.n8n') : t('admin.bots.add.llm'));
  const spaceChoices = spaces.map(space => ({ value: space.slug, label: space.name }));
  const steps: BotStep[] = draft.kind === 'llm'
    ? ['basics', 'behavior', 'knowledge', 'agents', 'sharing', 'review']
    : ['basics', 'behavior', 'knowledge', 'sharing', 'review'];
  const stepIndex = Math.max(0, steps.indexOf(step));
  const stepProblems: Partial<Record<BotStep, string[]>> = {
    basics: [
      ...(!draft.id.trim() ? [t('admin.bots.problem.id')] : []),
      ...(!draft.name.trim() ? [t('admin.bots.problem.name')] : []),
    ],
    behavior: [
      ...(draft.kind === 'llm'
        ? (!draft.system_prompt?.trim() ? [t('admin.bots.problem.systemPrompt')] : [])
        : (!draft.webhook_url?.trim() ? [t('admin.bots.problem.webhook')] : [])),
      ...guardRuleErrors,
    ],
    agents: agentErrors,
  };
  const goTo = (next: BotStep) => setStep(next);

  return <Modal size="lg" title={modalTitle} onClose={onClose} footer={<>
    <Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t('common.cancel')}</Button>
    {stepIndex > 0 && <Button type="button" variant="outline" onClick={() => goTo(steps[stepIndex - 1])} disabled={saving}>{t('admin.bots.nav.back')}</Button>}
    {stepIndex < steps.length - 1 && <Button type="button" variant={bot ? 'outline' : 'default'} onClick={() => goTo(steps[stepIndex + 1])}>{t('admin.bots.nav.next')}</Button>}
    {(bot || step === 'review') && <Button type="submit" form="bot-editor-form" disabled={!canSave}>{saving ? t('admin.bots.saving') : t('admin.bots.save')}</Button>}
  </>}>
    <ErrorNotice message={error} />
    <nav aria-label={t('admin.bots.nav.stepsLabel')} className="mb-5">
      <ol className="flex flex-wrap gap-2">
        {steps.map((item, index) => {
          const problems = stepProblems[item]?.length ?? 0;
          return <li key={item}>
            <button type="button" onClick={() => goTo(item)} aria-current={item === step ? 'step' : undefined}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${item === step ? 'border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-ink)]' : 'border-[var(--line-2)] text-[var(--ink-2)] hover:bg-[var(--hover)]'}`}>
              <span className="text-xs tabular-nums">{index + 1}</span>{t(`admin.bots.step.${item}`)}{problems > 0 && <span className="h-2 w-2 rounded-full bg-amber-500" aria-label={t('admin.bots.nav.stepHasProblems')} />}
            </button>
          </li>;
        })}
      </ol>
    </nav>
    <form id="bot-editor-form" className="space-y-5" onSubmit={save}>
      {step === 'basics' && <section className="space-y-4" aria-label={t('admin.bots.step.basics')}>
        <Field label={t('admin.bots.field.kind.label')}><select className={inputClass} value={draft.kind} disabled={Boolean(bot)} onChange={event => set('kind', event.target.value as BotDraft['kind'])}><option value="llm">{t('admin.bots.field.kind.optionLlm')}</option><option value="n8n">{t('admin.bots.field.kind.optionN8n')}</option></select></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('admin.bots.field.id.label')} hint={bot ? t('admin.bots.field.id.hintExisting') : t('admin.bots.field.id.hintNew')}><input className={inputClass} required disabled={Boolean(bot)} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" value={draft.id} onChange={event => set('id', event.target.value.toLowerCase())} placeholder={t('admin.bots.field.id.placeholder')} /></Field>
          <Field label={t('admin.bots.field.name.label')}><input className={inputClass} required value={draft.name} onChange={event => set('name', event.target.value)} placeholder={t('admin.bots.field.name.placeholder')} /></Field>
        </div>
        <Field label={t('admin.bots.field.description')}><textarea className={inputClass} rows={3} value={draft.description || ''} onChange={event => set('description', event.target.value || null)} /></Field>
        <Toggle checked={draft.enabled} onChange={value => set('enabled', value)} label={t('admin.bots.toggle.enabled')} />
      </section>}

      {step === 'behavior' && <section className="space-y-4" aria-label={t('admin.bots.step.behavior')}>
        {draft.kind === 'n8n' ? <>
          <Field label={t('admin.bots.field.webhook.label')} hint={t('admin.bots.field.webhook.hint')}><input className={inputClass} type="url" required value={draft.webhook_url || ''} onChange={event => set('webhook_url', event.target.value)} placeholder="https://n8n.example.com/webhook/weave-agent" /></Field>
          <div className="grid gap-4 sm:grid-cols-2"><Field label={t('admin.bots.field.bearerToken.label')} hint={t('admin.bots.field.bearerToken.hint')}><div className="relative"><KeyRound className="pointer-events-none absolute left-3 top-4 h-4 w-4 text-slate-400" /><input className={`${inputClass} pl-9`} type="password" autoComplete="new-password" disabled value="" placeholder={draft.has_auth_token ? t('admin.bots.field.bearerToken.placeholderStored') : t('admin.bots.field.bearerToken.placeholderPending')} readOnly /></div></Field><Field label={t('admin.bots.field.timeout.label')} hint={t('admin.bots.field.timeout.hint')}><input className={inputClass} type="number" min={1} max={14400} value={draft.timeout_seconds} onChange={event => set('timeout_seconds', Number(event.target.value) || 120)} /></Field></div>
          <Toggle checked={draft.streaming} onChange={value => set('streaming', value)} label={t('admin.bots.toggle.streaming')} />
        </> : <>
          <Field label={t('admin.bots.field.systemPrompt.label')}><textarea className={inputClass} rows={12} required value={draft.system_prompt || ''} onChange={event => set('system_prompt', event.target.value)} placeholder={t('admin.bots.field.systemPrompt.placeholder')} /></Field>
          <Field label={t('admin.bots.field.temperature')}><input className={inputClass} type="number" min={0} max={2} step={0.1} value={draft.temperature ?? 0.2} onChange={event => set('temperature', Number(event.target.value))} /></Field>
          <Field label={t('admin.bots.field.llmEndpoint.label')} hint={t('admin.bots.field.llmEndpoint.hint')}>
            <select className={inputClass} value={draft.llm_endpoint ?? ''} onChange={event => set('llm_endpoint', event.target.value || null)}>
              <option value="">{t('admin.bots.field.llmEndpoint.central')}</option>
              {ownEndpointOptions.map(endpoint => <option key={endpoint.id} value={endpoint.id}>{endpoint.name || endpoint.id}{endpoint.model ? ` · ${endpoint.model}` : ''}{endpoint.enabled ? '' : ` (${t('admin.chatProvider.endpoints.disabled')})`}</option>)}
            </select>
          </Field>
          <ScopeChoices
            title={t('admin.bots.field.llmEndpoints.label')}
            emptyLabel={t('admin.bots.field.llmEndpoints.hint')}
            items={[
              { value: '*', label: t('admin.bots.field.llmEndpoints.all') },
              ...endpoints.map(endpoint => ({ value: endpoint.id, label: endpoint.id === 'default' ? t('admin.bots.field.llmEndpoint.central') : endpoint.name || endpoint.id })),
            ]}
            selected={draft.llm_endpoints ?? []}
            onToggle={value => set('llm_endpoints', (draft.llm_endpoints ?? []).includes(value) ? (draft.llm_endpoints ?? []).filter(item => item !== value) : [...(draft.llm_endpoints ?? []), value])}
          />
          <Toggle checked={Boolean(intake)} onChange={value => set('intake', value ? emptyIntake() : null)} label={t('admin.bots.toggle.intake')} />
          {intake && <div className="space-y-4 rounded-xl border border-[var(--line-2)] p-4">
            <p className="text-sm text-[var(--muted)]">{t('admin.bots.intake.hint')}</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('admin.bots.field.intakeQuestion.label')}><input className={inputClass} maxLength={500} value={intake.question} onChange={event => set('intake', { ...intake, question: event.target.value })} placeholder={t('admin.bots.field.intakeQuestion.placeholder')} /></Field>
              <Field label={t('admin.bots.field.intakeLabel.label')} hint={t('admin.bots.field.intakeLabel.hint')}><input className={inputClass} maxLength={100} value={intake.label ?? ''} onChange={event => set('intake', { ...intake, label: event.target.value || null })} placeholder={t('admin.bots.field.intakeLabel.placeholder')} /></Field>
            </div>
            <Field label={t('admin.bots.field.intakeOptions.label')} hint={t('admin.bots.field.intakeOptions.hint')}><textarea className={inputClass} rows={4} value={intake.options.join('\n')} onChange={event => set('intake', { ...intake, options: event.target.value.split('\n') })} /></Field>
            <Toggle checked={intake.allow_free_text} onChange={value => set('intake', { ...intake, allow_free_text: value })} label={t('admin.bots.toggle.intakeFreeText')} />
          </div>}
        </>}
        <BlockedTopicsEditor topics={draft.blocked_topics ?? []} onChange={value => set('blocked_topics', value)} reply={draft.blocked_topic_reply ?? ''} onReplyChange={value => set('blocked_topic_reply', value || null)} />
      </section>}

      {step === 'knowledge' && <section className="space-y-4" aria-label={t('admin.bots.step.knowledge')}>
        {draft.kind === 'llm' && <Toggle checked={Boolean(draft.retrieval_enabled)} onChange={value => set('retrieval_enabled', value)} label={t('admin.bots.toggle.retrieval')} />}
        <ScopeChoices title={t('admin.bots.scope.spaces.title')} emptyLabel={t('admin.bots.scope.spaces.empty')} items={spaceChoices} selected={draft.collections} onToggle={value => toggleValue('collections', value)} />
        {draft.kind === 'llm' && draft.retrieval_enabled && <div className="grid gap-4 sm:grid-cols-3"><Field label={t('admin.bots.field.topK')}><input className={inputClass} type="number" min={1} value={draft.top_k} onChange={event => set('top_k', Number(event.target.value) || 20)} /></Field><Field label={t('admin.bots.field.finalK')}><input className={inputClass} type="number" min={1} max={draft.top_k} value={draft.final_k} onChange={event => set('final_k', Number(event.target.value) || 5)} /></Field><div className="flex items-end pb-2"><Toggle checked={Boolean(draft.rerank)} onChange={value => set('rerank', value)} label={t('admin.bots.field.reranking')} /></div></div>}
        {draft.kind === 'llm' && draft.retrieval_enabled && <Field label={t('admin.bots.field.departmentFilter.label')} hint={t('admin.bots.field.departmentFilter.hint')}><input className={inputClass} value={String(draft.retrieval_filters?.department || '')} onChange={event => set('retrieval_filters', { ...draft.retrieval_filters, department: event.target.value || null })} placeholder={t('admin.bots.field.departmentFilter.placeholder')} /></Field>}
        {draft.kind === 'llm' && draft.retrieval_enabled && <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t('admin.bots.field.minScore.label')} hint={t('admin.bots.field.minScore.hint')}><input className={inputClass} type="number" min={0} max={1} step={0.05} value={draft.min_score ?? ''} onChange={event => set('min_score', scoreOrNull(event.target.value))} /></Field>
          <Field label={t('admin.bots.field.clarifyBelow.label')} hint={t('admin.bots.field.clarifyBelow.hint')}><input className={inputClass} type="number" min={0} max={1} step={0.05} value={draft.clarify_below ?? ''} onChange={event => set('clarify_below', scoreOrNull(event.target.value))} /></Field>
          <Field label={t('admin.bots.field.weakContextBelow.label')} hint={t('admin.bots.field.weakContextBelow.hint')}><input className={inputClass} type="number" min={0} max={1} step={0.05} value={draft.weak_context_below ?? ''} onChange={event => set('weak_context_below', scoreOrNull(event.target.value))} /></Field>
        </div>}
        {draft.kind === 'llm' && draft.retrieval_enabled && <Toggle checked={draft.query_rewrite === true} onChange={value => set('query_rewrite', value ? true : null)} label={t('admin.bots.toggle.queryRewrite')} />}
        <Toggle checked={draft.require_sources} onChange={value => set('require_sources', value)} label={t('admin.bots.toggle.requireSources')} />
        {draft.require_sources && <Toggle checked={draft.uncertainty_hint !== false} onChange={value => set('uncertainty_hint', value)} label={t('admin.bots.toggle.uncertaintyHint')} />}
        {draft.require_sources && <Field label={t('admin.bots.field.noContextReply.label')}><textarea className={inputClass} rows={2} value={draft.no_context_reply} onChange={event => set('no_context_reply', event.target.value)} /></Field>}
        <Toggle checked={draft.show_sources !== false} onChange={value => set('show_sources', value)} label={t('admin.bots.toggle.showSources')} />
        <Field label={t('admin.bots.field.disclaimer.label')} hint={t('admin.bots.field.disclaimer.hint')}><input className={inputClass} maxLength={500} value={draft.disclaimer ?? ''} onChange={event => set('disclaimer', event.target.value)} placeholder={t('admin.bots.field.disclaimer.placeholder')} /></Field>
      </section>}

      {step === 'agents' && draft.kind === 'llm' && <section className="space-y-4" aria-label={t('admin.bots.step.agents')}>
        <Toggle
          checked={Boolean(draft.agent?.enabled)}
          onChange={value => set('agent', value ? (draft.agent ? { ...draft.agent, enabled: true } : emptyAgent()) : (draft.agent ? { ...draft.agent, enabled: false } : null))}
          label={t('admin.bots.toggle.agentMode')}
        />
        {draft.agent?.enabled
          ? <AgentEditor agent={draft.agent} spaces={spaces} endpoints={endpoints} onChange={next => set('agent', next)} errors={agentErrors} />
          : <p className="text-sm text-[var(--muted)]">{t('admin.bots.agent.offHint')}</p>}
      </section>}

      {step === 'sharing' && <BotSharing isPublic={draft.public} onPublicChange={value => set('public', value)} grants={draft.grants} onGrantsChange={value => set('grants', value)} teams={teams} canEditOwners disabled={saving} />}

      {step === 'review' && <section className="space-y-4" aria-label={t('admin.bots.step.review')}>
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
          <dt className="font-medium text-[var(--ink-2)]">{t('admin.bots.field.name.label')}</dt><dd>{draft.name || '—'} <span className="text-[var(--muted)]">({draft.id || '—'})</span></dd>
          <dt className="font-medium text-[var(--ink-2)]">{t('admin.bots.field.kind.label')}</dt><dd>{draft.kind === 'llm' ? t('admin.bots.field.kind.optionLlm') : t('admin.bots.field.kind.optionN8n')}</dd>
          <dt className="font-medium text-[var(--ink-2)]">{t('admin.bots.scope.spaces.title')}</dt><dd>{draft.collections.length ? draft.collections.map(slug => spaces.find(space => space.slug === slug)?.name ?? slug).join(', ') : t('admin.bots.spacesAllAuthorized')}</dd>
          {draft.kind === 'llm' && <><dt className="font-medium text-[var(--ink-2)]">{t('admin.bots.step.agents')}</dt><dd>{draft.agent?.enabled ? draft.agent.subagents.map(subagent => subagent.name || subagent.id).join(', ') || '—' : t('admin.bots.review.agentOff')}</dd></>}
          <dt className="font-medium text-[var(--ink-2)]">{t('admin.bots.review.usableBy')}</dt><dd>{draft.public ? t('admin.bots.teamsAll') : draft.grants.filter(grant => grant.role === 'user').map(grant => grant.name).join(', ') || t('admin.bots.teamsNone')}</dd>
          <dt className="font-medium text-[var(--ink-2)]">{t('admin.bots.review.owners')}</dt><dd>{draft.grants.filter(grant => grant.role === 'owner').map(grant => grant.name).join(', ') || '—'}</dd>
        </dl>
        {steps.some(item => stepProblems[item]?.length) ? <ul role="alert" className="list-disc space-y-1 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 pl-8 text-sm text-amber-800">
          {steps.flatMap(item => (stepProblems[item] ?? []).map(problem => <li key={`${item}-${problem}`}><button type="button" className="underline" onClick={() => goTo(item)}>{t(`admin.bots.step.${item}`)}</button>: {problem}</li>))}
        </ul> : <p className="text-sm text-emerald-700">{t('admin.bots.review.ready')}</p>}
      </section>}
    </form>
  </Modal>;
}

function ScopeChoices({ title, emptyLabel, items, selected, onToggle }: { title: string; emptyLabel: string; items: { value: string; label: string }[]; selected: string[]; onToggle: (value: string) => void }) {
  const { t } = useI18n();
  return <fieldset><legend className="text-sm font-medium text-slate-700">{title}</legend><p className="mt-1 text-xs text-slate-400">{emptyLabel}</p>{items.length ? <div className="mt-2 grid max-h-56 gap-2 overflow-y-auto rounded-xl border border-slate-200 p-3 sm:grid-cols-2 lg:grid-cols-3">{items.map(item => <label key={item.value} className="flex items-center gap-2 text-sm text-slate-700" title={item.value}><input type="checkbox" checked={selected.includes(item.value)} onChange={() => onToggle(item.value)} />{item.label}</label>)}</div> : <p className="mt-2 text-sm text-amber-700">{t('admin.bots.scope.noEntries')}</p>}</fieldset>;
}

function AgentEditor({
  agent, spaces, endpoints, onChange, errors,
}: { agent: AgentConfig; spaces: KnowledgeSpace[]; endpoints: LlmEndpointOption[]; onChange: (next: AgentConfig) => void; errors: string[] }) {
  const { t } = useI18n();
  const setLimits = (limits: Partial<AgentLimits>) => onChange({ ...agent, limits: { ...agent.limits, ...limits } });
  const setSubagent = (index: number, next: AgentSubagent) =>
    onChange({ ...agent, subagents: agent.subagents.map((item, i) => (i === index ? next : item)) });
  const removeSubagent = (index: number) => onChange({ ...agent, subagents: agent.subagents.filter((_, i) => i !== index) });
  const addSubagent = () => onChange({ ...agent, subagents: [...agent.subagents, emptySubagent(agent.subagents.map(s => s.id))] });

  const [selected, setSelected] = useState(0);
  const current = Math.min(selected, Math.max(0, agent.subagents.length - 1));
  const subagent = agent.subagents[current];

  return <div className="space-y-4">
    {errors.length > 0 && (
      <ul role="alert" className="list-disc space-y-1 rounded-xl border border-red-200 bg-red-50 px-4 py-3 pl-8 text-xs text-red-700">
        {errors.map(err => <li key={err}>{err}</li>)}
      </ul>
    )}
    <div className="grid gap-4 md:grid-cols-[14rem_1fr]">
      <div className="space-y-2">
        <p className="text-sm font-medium text-slate-700">{t('admin.bots.agent.listTitle')}</p>
        {agent.subagents.length === 0 && <p className="text-sm text-amber-700">{t('admin.bots.agent.noSubagents')}</p>}
        <ul className="space-y-2.5" aria-label={t('admin.bots.agent.listTitle')}>
          {agent.subagents.map((item, index) => <li key={index}>
            <button type="button" onClick={() => setSelected(index)} aria-current={index === current ? 'true' : undefined}
              className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${index === current ? 'border-[var(--accent)] bg-[var(--accent-soft)]' : 'border-[var(--line-2)] hover:bg-[var(--hover)]'}`}>
              <span className="block font-medium">{item.name || item.id || t('admin.bots.agent.unnamed')}</span>
              <span className="block truncate text-xs text-[var(--muted)]">{item.mission || '—'}</span>
            </button>
          </li>)}
        </ul>
        <Button type="button" variant="outline" size="sm" onClick={() => { addSubagent(); setSelected(agent.subagents.length); }}><Plus size={15} />{t('admin.bots.agent.addSubagent')}</Button>
      </div>
      <div className="min-w-0">
        {subagent
          ? <SubagentEditor key={current} subagent={subagent} spaces={spaces} endpoints={endpoints} onChange={next => setSubagent(current, next)} onRemove={() => { removeSubagent(current); setSelected(Math.max(0, current - 1)); }} />
          : <p className="rounded-xl border border-dashed border-[var(--line-2)] p-6 text-sm text-[var(--muted)]">{t('admin.bots.agent.selectHint')}</p>}
      </div>
    </div>

    <fieldset className="rounded-xl bg-slate-50 p-3">
      <legend className="px-1 text-sm font-medium text-slate-700">{t('admin.bots.agent.limitsLegend')}</legend>
      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label={t('admin.bots.agent.field.maxParallel')}><input className={inputClass} type="number" min={1} max={20} value={agent.limits.max_parallel} onChange={event => setLimits({ max_parallel: Number(event.target.value) || 1 })} /></Field>
        <Field label={t('admin.bots.agent.field.maxFollowups')}><input className={inputClass} type="number" min={0} max={10} value={agent.limits.max_followups} onChange={event => setLimits({ max_followups: Number(event.target.value) || 0 })} /></Field>
        <Field label={t('admin.bots.agent.field.budgetSearches')}><input className={inputClass} type="number" min={0} max={500} value={agent.limits.budget_searches} onChange={event => setLimits({ budget_searches: Number(event.target.value) || 0 })} /></Field>
        <Field label={t('admin.bots.agent.field.totalTimeout')}><input className={inputClass} type="number" min={1} max={3600} value={agent.limits.timeout_seconds} onChange={event => setLimits({ timeout_seconds: Number(event.target.value) || 120 })} /></Field>
      </div>
    </fieldset>
  </div>;
}

function SubagentEditor({
  subagent, spaces, endpoints, onChange, onRemove,
}: { subagent: AgentSubagent; spaces: KnowledgeSpace[]; endpoints: LlmEndpointOption[]; onChange: (next: AgentSubagent) => void; onRemove: () => void }) {
  const { t } = useI18n();
  // Auto-derives the id from the name until the id has been edited by hand
  // (tracked locally, never sent to the backend) -- mirrors how the
  // top-level bot editor above keeps the id user-editable while offering a
  // sensible default.
  const [idTouched, setIdTouched] = useState(Boolean(subagent.name) && subagent.id !== slugify(subagent.name));
  const setModel = (updates: Partial<AgentSubagentModel>) =>
    onChange({ ...subagent, model: { provider: 'openai', model: '', supports_tools: false, ...subagent.model, ...updates } });
  const setLimits = (limits: Partial<AgentSubagentLimits>) => onChange({ ...subagent, limits: { ...subagent.limits, ...limits } });
  const setFilter = (key: 'team' | 'department' | 'source' | 'language' | 'document_type', value: string) =>
    onChange({ ...subagent, filters: { ...subagent.filters, [key]: value || undefined } });
  const setTagsFilter = (value: string) =>
    onChange({
      ...subagent,
      filters: {
        ...subagent.filters,
        tags: value.trim() ? value.split(',').map(tag => tag.trim()).filter(Boolean) : undefined,
      },
    });
  const toggleCollection = (slug: string) =>
    onChange({
      ...subagent,
      collections: subagent.collections.includes(slug)
        ? subagent.collections.filter(item => item !== slug)
        : [...subagent.collections, slug],
    });

  return <div className="space-y-4 rounded-xl border border-slate-200 p-4">
    <div className="flex items-start justify-between gap-2">
      <div className="grid flex-1 gap-3 sm:grid-cols-2">
        <Field label={t('admin.bots.field.subagent.name')}>
          <input
            className={inputClass}
            value={subagent.name}
            onChange={event => {
              const name = event.target.value;
              onChange({ ...subagent, name, id: idTouched ? subagent.id : slugify(name) });
            }}
            placeholder={t('admin.bots.field.subagent.namePlaceholder')}
          />
        </Field>
        <Field label={t('admin.bots.field.subagent.id.label')} hint={t('admin.bots.field.subagent.id.hint')}>
          <input
            className={inputClass}
            value={subagent.id}
            onChange={event => { setIdTouched(true); onChange({ ...subagent, id: event.target.value.toLowerCase() }); }}
            placeholder={t('admin.bots.field.subagent.id.placeholder')}
          />
        </Field>
      </div>
      <Button type="button" variant="ghost" size="sm" onClick={onRemove} aria-label={t('admin.bots.subagent.removeAria', { name: subagent.name || subagent.id })}><Trash2 size={15} /></Button>
    </div>
    <Field label={t('admin.bots.field.description')}><input className={inputClass} value={subagent.description || ''} onChange={event => onChange({ ...subagent, description: event.target.value || null })} /></Field>
    <Field label={t('admin.bots.field.mission.label')}><textarea className={inputClass} rows={5} value={subagent.mission} onChange={event => onChange({ ...subagent, mission: event.target.value })} placeholder={t('admin.bots.field.mission.placeholder')} /></Field>
    <ScopeChoices title={t('admin.bots.scope.spaces.title')} emptyLabel={t('admin.bots.subagent.scope.empty')} items={spaces.map(space => ({ value: space.slug, label: space.name }))} selected={subagent.collections} onToggle={toggleCollection} />
    <Toggle checked={subagent.include_uncollected} onChange={value => onChange({ ...subagent, include_uncollected: value })} label={t('admin.bots.toggle.includeUncollected')} />
    <details className="rounded-xl border border-slate-200 p-3">
    <summary className="cursor-pointer text-sm font-medium text-slate-700">{t('admin.bots.subagent.filtersSummary')}</summary>
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <Field label={t('admin.bots.field.filter.team')}><input className={inputClass} value={subagent.filters.team || ''} onChange={event => setFilter('team', event.target.value)} /></Field>
      <Field label={t('admin.bots.field.filter.department')}><input className={inputClass} value={subagent.filters.department || ''} onChange={event => setFilter('department', event.target.value)} /></Field>
      <Field label={t('admin.bots.field.filter.source')}><input className={inputClass} value={subagent.filters.source || ''} onChange={event => setFilter('source', event.target.value)} /></Field>
      <Field label={t('admin.bots.field.filter.language')}><input className={inputClass} value={subagent.filters.language || ''} onChange={event => setFilter('language', event.target.value)} /></Field>
      <Field label={t('admin.bots.field.filter.documentType')}><input className={inputClass} value={subagent.filters.document_type || ''} onChange={event => setFilter('document_type', event.target.value)} /></Field>
      <Field label={t('admin.bots.field.filter.tags')} hint={t('admin.bots.field.filter.tagsHint')}>
        <input
          className={inputClass}
          value={(subagent.filters.tags || []).join(', ')}
          onChange={event => setTagsFilter(event.target.value)}
        />
      </Field>
    </div>
    </details>
    <fieldset className="rounded-xl bg-slate-50 p-3"><legend className="px-1 text-sm font-medium text-slate-700">{t('admin.bots.subagent.limitsLegend')}</legend>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        <Field label={t('admin.bots.subagent.field.maxSearches')}><input className={inputClass} type="number" min={1} max={50} value={subagent.limits.max_searches} onChange={event => setLimits({ max_searches: Number(event.target.value) || 1 })} /></Field>
        <Field label={t('admin.bots.subagent.field.maxResults')}><input className={inputClass} type="number" min={1} max={50} value={subagent.limits.max_results} onChange={event => setLimits({ max_results: Number(event.target.value) || 1 })} /></Field>
        <Field label={t('admin.bots.subagent.field.timeout')}><input className={inputClass} type="number" min={1} max={3600} value={subagent.limits.timeout_seconds} onChange={event => setLimits({ timeout_seconds: Number(event.target.value) || 60 })} /></Field>
      </div>
    </fieldset>
    {/* Only tool-capable endpoints: a subagent researches via tool calls. */}
    <Field label={t('admin.bots.subagent.field.endpoint.label')} hint={t('admin.bots.subagent.field.endpoint.hint')}>
      <select className={inputClass} value={subagent.endpoint ?? ''} onChange={event => onChange({ ...subagent, endpoint: event.target.value || null })}>
        <option value="">{t('admin.bots.subagent.field.endpoint.inherit')}</option>
        {endpoints.filter(endpoint => endpoint.supports_tools || endpoint.id === subagent.endpoint).map(endpoint => <option key={endpoint.id} value={endpoint.id}>{endpoint.id === 'default' ? t('admin.bots.field.llmEndpoint.central') : endpoint.name || endpoint.id}{endpoint.model ? ` · ${endpoint.model}` : ''}</option>)}
        {subagent.endpoint && !endpoints.some(endpoint => endpoint.id === subagent.endpoint) && <option value={subagent.endpoint}>{subagent.endpoint}</option>}
      </select>
    </Field>
    <details className="rounded-xl border border-slate-200 p-3">
      <summary className="cursor-pointer text-sm font-medium text-slate-700">{t('admin.bots.subagent.modelOverride.summary')}</summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label={t('admin.bots.field.provider')}><input className={inputClass} value={subagent.model?.provider || ''} onChange={event => setModel({ provider: event.target.value })} placeholder="openai" /></Field>
        <Field label={t('admin.bots.field.model')}><input className={inputClass} value={subagent.model?.model || ''} onChange={event => setModel({ model: event.target.value })} placeholder="gpt-4o-mini" /></Field>
      </div>
      <Toggle checked={Boolean(subagent.model?.supports_tools)} onChange={value => setModel({ supports_tools: value })} label={t('admin.bots.subagent.toggle.supportsTools')} />
      <Button type="button" variant="ghost" size="sm" className="mt-2" onClick={() => onChange({ ...subagent, model: null })}>{t('admin.bots.subagent.removeOverride')}</Button>
    </details>
  </div>;
}

function BlockedTopicsEditor({ topics, onChange, reply, onReplyChange }: { topics: BlockedTopic[]; onChange: (topics: BlockedTopic[]) => void; reply: string; onReplyChange: (reply: string) => void }) {
  const { t } = useI18n();
  const update = (index: number, patch: Partial<BlockedTopic>) => onChange(topics.map((topic, i) => i === index ? { ...topic, ...patch } : topic));
  return <div className="space-y-3">
    <div><h4 className="text-sm font-semibold text-[var(--ink)]">{t('admin.bots.blockedTopics.title')}</h4><p className="text-sm text-[var(--muted)]">{t('admin.bots.blockedTopics.hint')}</p></div>
    {topics.map((topic, index) => <div key={index} className="space-y-3 rounded-xl border border-[var(--line-2)] p-4">
      <div className="flex items-start gap-2">
        <div className="grid flex-1 gap-3 sm:grid-cols-2">
          <Field label={t('admin.bots.field.blockedTopicName')}><input className={inputClass} maxLength={100} value={topic.name} onChange={event => update(index, { name: event.target.value })} /></Field>
          <Field label={t('admin.bots.field.blockedTopicKeywords.label')} hint={t('admin.bots.field.blockedTopicKeywords.hint')}><input className={inputClass} value={topic.keywords.join(',')} onChange={event => update(index, { keywords: event.target.value.split(',') })} /></Field>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange(topics.filter((_, i) => i !== index))} aria-label={t('admin.bots.blockedTopics.remove', { name: topic.name })}><Trash2 size={15} /></Button>
      </div>
      <Field label={t('admin.bots.field.blockedTopicDescription.label')} hint={t('admin.bots.field.blockedTopicDescription.hint')}><textarea className={inputClass} rows={2} maxLength={500} value={topic.description ?? ''} onChange={event => update(index, { description: event.target.value || null })} /></Field>
    </div>)}
    <Button type="button" variant="outline" size="sm" onClick={() => onChange([...topics, { name: '', description: null, keywords: [] }])}><Plus size={15} />{t('admin.bots.blockedTopics.add')}</Button>
    {topics.length > 0 && <Field label={t('admin.bots.field.blockedTopicReply.label')} hint={t('admin.bots.field.blockedTopicReply.hint')}><textarea className={inputClass} rows={2} maxLength={2000} value={reply} onChange={event => onReplyChange(event.target.value)} placeholder={t('admin.bots.field.blockedTopicReply.placeholder')} /></Field>}
  </div>;
}
