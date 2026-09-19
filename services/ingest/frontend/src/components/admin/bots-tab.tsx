'use client';

import { useState, type FormEvent } from 'react';
import { Bot, KeyRound, Pencil, Plus, Trash2, Workflow } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import type { Team } from '@/lib/auth-types';
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
  teams: string[];
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
  agent?: AgentConfig | null;
  created_at: string;
  updated_at: string;
  source?: 'managed' | 'runtime';
  editable?: boolean;
};

type BotDraft = Omit<ManagedBot, 'has_auth_token' | 'created_at' | 'updated_at'> & {
  auth_token: string;
  clear_auth_token: boolean;
  has_auth_token: boolean;
};

const emptyDraft = (): BotDraft => ({
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
  teams: [],
  collections: [],
  require_sources: true,
  no_context_reply: 'Ich habe dazu keine belegten Informationen gefunden.',
  system_prompt: '',
  temperature: 0.2,
  retrieval_enabled: false,
  retrieval_filters: {},
  top_k: 20,
  final_k: 5,
  rerank: true,
  include_uncollected: true,
  agent: null,
});

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

/** Client-side mirror of Weave-Runtime's own `AgentConfig` validation
 * rules (services/runtime/backend/app/schemas/bot.py's `AgentConfig`/
 * `SubagentConfig`) -- catches a malformed agent-mode configuration in the
 * editor itself, with a German message, instead of only surfacing it as a
 * 422 from Weave-Ingest's own mirror (schemas/managed_bots.py) after
 * "Bot speichern" already failed. Returns an empty array when `agent` is
 * `null` or disabled, since a disabled/absent agent block has nothing to
 * validate. */
function validateAgent(agent: AgentConfig | null): string[] {
  if (!agent || !agent.enabled) return [];
  const errors: string[] = [];
  if (agent.subagents.length === 0) {
    errors.push('Mindestens ein Subagent ist erforderlich, wenn der Agentenmodus aktiviert ist.');
  }
  const ids = agent.subagents.map(subagent => subagent.id);
  const duplicates = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
  if (duplicates.length > 0) {
    errors.push(`Die Subagenten-IDs müssen eindeutig sein: ${duplicates.join(', ')}.`);
  }
  for (const subagent of agent.subagents) {
    const label = subagent.name.trim() || subagent.id || 'Unbenannter Subagent';
    if (!subagent.id.trim()) errors.push(`Subagent "${label}": Die ID darf nicht leer sein.`);
    if (!subagent.name.trim()) errors.push(`Subagent "${label}": Der Name darf nicht leer sein.`);
    if (!subagent.mission.trim()) errors.push(`Subagent "${label}": Der fachliche Auftrag darf nicht leer sein.`);
    if (subagent.collections.length === 0 && !subagent.include_uncollected) {
      errors.push(
        `Subagent "${label}": Mindestens ein Wissensbereich ist erforderlich, oder "Dokumente ohne ` +
        'Wissensbereich einbeziehen" muss aktiviert sein.'
      );
    }
    if (subagent.model && !subagent.model.model.trim()) {
      errors.push(`Subagent "${label}": Das Modell des Modell-Override darf nicht leer sein.`);
    }
  }
  return errors;
}

export function BotsTab() {
  const bots = useAdminList<ManagedBot>('/api/v1/auth/admin/bots');
  const teams = useAdminList<Team>('/api/v1/auth/admin/teams');
  const spaces = useAdminList<KnowledgeSpace>('/api/v1/collections');
  const [editing, setEditing] = useState<ManagedBot | 'new' | null>(null);
  const [deleting, setDeleting] = useState<ManagedBot | null>(null);
  const [notice, setNotice] = useState('');

  const loading = bots.loading || teams.loading || spaces.loading;
  const error = bots.error || teams.error || spaces.error;

  return <div className="space-y-6">
    <SectionCard
      title="Bots"
      description="Lokale Konfigurationsbots und verwaltete n8n-Bots. Verwaltete Bots können hier bearbeitet und Teams zugewiesen werden."
      actions={<Button variant="outline" size="sm" onClick={() => { setEditing('new'); setNotice(''); }}><Plus size={15} />Bot hinzufügen</Button>}
    >
      <ErrorNotice message={error} />
      {notice && <p role="status" className="mb-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}
      {loading ? <LoadingState label="Bots und Berechtigungen werden geladen…" /> : bots.items.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <Workflow className="h-9 w-9 text-slate-300" />
          <div><p className="font-medium text-slate-800">Noch keine verwalteten Bots</p><p className="mt-1 text-sm text-slate-500">Lege einen Bot an, damit berechtigte Nutzer ihn im Chat auswählen können.</p></div>
          <Button variant="outline" size="sm" onClick={() => setEditing('new')}><Plus size={15} />Ersten Bot hinzufügen</Button>
        </div>
      ) : <ul className={bots.items.length > 3 ? 'grid gap-3 md:grid-cols-2 xl:grid-cols-3' : 'divide-y divide-slate-100'}>
        {bots.items.map(item => <li key={item.id} className={`flex flex-wrap items-start justify-between gap-4 ${bots.items.length > 3 ? 'min-w-0 rounded-xl border border-slate-200 p-3' : 'py-4'}`}>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><Bot size={18} className="text-emerald-700" /><h3 className="font-semibold text-slate-950">{item.name}</h3><Badge tone={item.enabled ? 'emerald' : 'slate'}>{item.enabled ? 'Aktiv' : 'Inaktiv'}</Badge><Badge tone={item.source === 'runtime' ? 'slate' : 'emerald'}>{item.source === 'runtime' ? 'Konfigurationsbot' : 'Verwaltet'}</Badge>{item.streaming && <Badge tone="amber">Streaming konfiguriert</Badge>}{item.has_auth_token && <Badge tone="amber">Bearer-Token hinterlegt</Badge>}</div>
            <p className="mt-1 text-sm text-slate-600">{item.description || item.id}</p>
            {item.webhook_url && <p className="mt-2 break-all text-xs text-slate-400">{item.webhook_url}</p>}
            <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500"><span>Anwendergruppen: {item.teams.length ? item.teams.join(', ') : 'alle'}</span><span>Wissensbereiche: {item.collections.length ? item.collections.length : 'alle berechtigten'}</span></div>
          </div>
          {item.editable !== false && <div className="flex gap-1"><Button variant="ghost" size="sm" onClick={() => setEditing(item)} aria-label={`${item.name} bearbeiten`}><Pencil size={15} />Bearbeiten</Button><Button variant="ghost" size="sm" onClick={() => setDeleting(item)} aria-label={`${item.name} löschen`}><Trash2 size={15} /></Button></div>}
        </li>)}
      </ul>}
    </SectionCard>

    <SectionCard title="Sicherheitsmodell" description="Die Auswahl im Formular erteilt keine zusätzlichen Dokumentrechte.">
      <div className="grid gap-4 text-sm text-slate-600 md:grid-cols-3">
        <p><strong className="block text-slate-900">Anwendergruppen</strong>Nur Mitglieder der ausgewählten Teams sehen und verwenden den Bot.</p>
        <p><strong className="block text-slate-900">Wissensbereiche</strong>Der effektive Scope ist immer die Schnittmenge aus Bot-Auswahl und Nutzerrechten.</p>
        <p><strong className="block text-slate-900">n8n</strong>Die bestehende JSON-Anbindung übermittelt ein kurzlebiges Delegations-Token. Die Bearer-Weitergabe wird erst nach der gesonderten Sicherheitsfreigabe aktiviert.</p>
      </div>
    </SectionCard>

    {editing && <BotEditor bot={editing === 'new' ? null : editing} teams={teams.items} spaces={spaces.items} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); setNotice('Bot-Konfiguration gespeichert. Sie gilt ab der nächsten Anfrage.'); await bots.reload(); }} />}
    {deleting && <ConfirmDialog title="Bot löschen" body={<p>Den Bot <strong className="text-slate-950">{deleting.name}</strong> löschen? Er verschwindet aus der Chat-Auswahl.</p>} confirmLabel="Bot löschen" onClose={() => setDeleting(null)} onConfirm={async () => { await apiSend(`/api/v1/auth/admin/bots/${encodeURIComponent(deleting.id)}`, { method: 'DELETE' }); setDeleting(null); setNotice('Bot gelöscht.'); await bots.reload(); }} />}
  </div>;
}

function BotEditor({ bot, teams, spaces, onClose, onSaved }: { bot: ManagedBot | null; teams: Team[]; spaces: KnowledgeSpace[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [draft, setDraft] = useState<BotDraft>(() => bot ? { ...bot, auth_token: '', clear_auth_token: false } : emptyDraft());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof BotDraft>(key: K, value: BotDraft[K]) => setDraft(current => ({ ...current, [key]: value }));
  const toggleValue = (key: 'teams' | 'collections', value: string) => set(key, draft[key].includes(value) ? draft[key].filter(item => item !== value) : [...draft[key], value]);
  const agentErrors = draft.kind === 'llm' ? validateAgent(draft.agent ?? null) : [];
  const canSave = Boolean(
    draft.id.trim() && draft.name.trim() &&
    (draft.kind === 'llm' ? draft.system_prompt?.trim() : draft.webhook_url?.trim()) &&
    agentErrors.length === 0 && !saving
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
      teams: draft.teams,
      collections: draft.collections,
      require_sources: draft.require_sources,
      no_context_reply: draft.no_context_reply,
      agent: draft.kind === 'llm' && draft.agent?.enabled ? draft.agent : null,
      ...(!bot ? { id: draft.id } : {}),
    };
    try {
      await apiJson(bot ? `/api/v1/auth/admin/bots/${encodeURIComponent(bot.id)}` : '/api/v1/auth/admin/bots', {
        method: bot ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      await onSaved();
    } catch (err) { setError(errorMessage(err)); setSaving(false); }
  }

  return <Modal title={bot ? `${bot.name} bearbeiten` : `${draft.kind === 'n8n' ? 'n8n' : 'LLM'}-Bot hinzufügen`} onClose={onClose}>
    <ErrorNotice message={error} />
    <form className="space-y-5" onSubmit={save}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Bot-ID" hint={bot ? 'Die technische ID bleibt unverändert.' : 'Kleinbuchstaben, Zahlen und Bindestriche.'}><input className={inputClass} required disabled={Boolean(bot)} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" value={draft.id} onChange={event => set('id', event.target.value.toLowerCase())} placeholder="service-assistent" /></Field>
        <Field label="Anzeigename"><input className={inputClass} required value={draft.name} onChange={event => set('name', event.target.value)} placeholder="Service-Assistent" /></Field>
      </div>
      <Field label="Beschreibung"><textarea className={inputClass} rows={2} value={draft.description || ''} onChange={event => set('description', event.target.value || null)} /></Field>
      <Field label="Bot-Typ"><select className={inputClass} value={draft.kind} onChange={event => set('kind', event.target.value as BotDraft['kind'])}><option value="llm">LLM-Bot mit Wissenssuche</option><option value="n8n">n8n-Bot</option></select></Field>
      {draft.kind === 'n8n' ? <Field label="n8n-Webhook" hint="Muss zusätzlich in N8N_ALLOWED_BASE_URLS des Runtime-Deployments freigegeben sein."><input className={inputClass} type="url" required value={draft.webhook_url || ''} onChange={event => set('webhook_url', event.target.value)} placeholder="https://n8n.example.com/webhook/weave-agent" /></Field> : <Field label="System-Prompt"><textarea className={inputClass} rows={5} required value={draft.system_prompt || ''} onChange={event => set('system_prompt', event.target.value)} placeholder="Du beantwortest Fragen ausschließlich anhand belegter Quellen." /></Field>}
      <div className="grid gap-4 sm:grid-cols-2"><Field label="Bearer-Token" hint="Die verschlüsselte Speicherung ist vorbereitet. Die Weitergabe an n8n wird nach der Sicherheitsfreigabe aktiviert."><div className="relative"><KeyRound className="pointer-events-none absolute left-3 top-4 h-4 w-4 text-slate-400" /><input className={`${inputClass} pl-9`} type="password" autoComplete="new-password" disabled value="" placeholder={draft.has_auth_token ? 'Token ist hinterlegt' : 'Aktivierung ausstehend'} readOnly /></div></Field><Field label="Timeout (Sekunden)" hint="Leerlauf-Timeout zwischen zwei Antwortteilen; für lange Agentenläufe Streaming aktivieren."><input className={inputClass} type="number" min={1} max={14400} value={draft.timeout_seconds} onChange={event => set('timeout_seconds', Number(event.target.value) || 120)} /></Field></div>
      <div className="space-y-3 rounded-xl bg-slate-50 p-4"><Toggle checked={draft.enabled} onChange={value => set('enabled', value)} label="Bot im Chat anbieten" />{draft.kind === 'n8n' && <Toggle checked={draft.streaming} onChange={value => set('streaming', value)} label="n8n-Streaming (Antwort wird fortlaufend übertragen)" />}<Toggle checked={draft.require_sources} onChange={value => set('require_sources', value)} label="Antwort nur mit belegten Quellen ausgeben" />{draft.kind === 'llm' && <Toggle checked={Boolean(draft.retrieval_enabled)} onChange={value => set('retrieval_enabled', value)} label="Wissen durchsuchen" />}</div>
      {draft.kind === 'llm' && draft.retrieval_enabled && <div className="grid gap-4 sm:grid-cols-4"><Field label="Top-K"><input className={inputClass} type="number" min={1} value={draft.top_k} onChange={event => set('top_k', Number(event.target.value) || 20)} /></Field><Field label="Final-K"><input className={inputClass} type="number" min={1} max={draft.top_k} value={draft.final_k} onChange={event => set('final_k', Number(event.target.value) || 5)} /></Field><Field label="Temperatur"><input className={inputClass} type="number" min={0} max={2} step={0.1} value={draft.temperature ?? 0.2} onChange={event => set('temperature', Number(event.target.value))} /></Field><Toggle checked={Boolean(draft.rerank)} onChange={value => set('rerank', value)} label="Reranking" /></div>}
      {draft.kind === 'llm' && draft.retrieval_enabled && <Field label="Abteilung filtern" hint="Optionaler Metadatenfilter für die Wissenssuche."><input className={inputClass} value={String(draft.retrieval_filters?.department || '')} onChange={event => set('retrieval_filters', { ...draft.retrieval_filters, department: event.target.value || null })} placeholder="z. B. legal" /></Field>}
      {draft.require_sources && <Field label="Antwort ohne belegte Quellen"><textarea className={inputClass} rows={2} value={draft.no_context_reply} onChange={event => set('no_context_reply', event.target.value)} /></Field>}
      {draft.kind === 'llm' && (
        <div className="space-y-3 rounded-xl border border-slate-200 p-4">
          <Toggle
            checked={Boolean(draft.agent?.enabled)}
            onChange={value => set('agent', value ? (draft.agent ?? emptyAgent()) : (draft.agent ? { ...draft.agent, enabled: false } : null))}
            label="Agentenmodus (Subagenten recherchieren mit eigenem Suchbudget)"
          />
          {draft.agent?.enabled && (
            <AgentEditor agent={draft.agent} spaces={spaces} onChange={next => set('agent', next)} errors={agentErrors} />
          )}
        </div>
      )}
      <ScopeChoices title="Anwendergruppen" emptyLabel="Keine Auswahl: alle Anwendergruppen dürfen den Bot verwenden." items={teams.map(team => team.name)} selected={draft.teams} onToggle={value => toggleValue('teams', value)} />
      <ScopeChoices title="Wissensbereiche" emptyLabel="Keine Auswahl: alle Wissensbereiche, für die der jeweilige Nutzer berechtigt ist." items={spaces.map(space => space.slug)} selected={draft.collections} onToggle={value => toggleValue('collections', value)} />
      <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-4"><Button type="button" variant="outline" onClick={onClose} disabled={saving}>Abbrechen</Button><Button type="submit" disabled={!canSave}>{saving ? 'Wird gespeichert…' : 'Bot speichern'}</Button></div>
    </form>
  </Modal>;
}

function ScopeChoices({ title, emptyLabel, items, selected, onToggle }: { title: string; emptyLabel: string; items: string[]; selected: string[]; onToggle: (value: string) => void }) {
  return <fieldset><legend className="text-sm font-medium text-slate-700">{title}</legend><p className="mt-1 text-xs text-slate-400">{emptyLabel}</p>{items.length ? <div className="mt-2 grid max-h-36 gap-2 overflow-y-auto rounded-xl border border-slate-200 p-3 sm:grid-cols-2">{items.map(item => <label key={item} className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={selected.includes(item)} onChange={() => onToggle(item)} />{item}</label>)}</div> : <p className="mt-2 text-sm text-amber-700">Noch keine Einträge verfügbar.</p>}</fieldset>;
}

function AgentEditor({
  agent, spaces, onChange, errors,
}: { agent: AgentConfig; spaces: KnowledgeSpace[]; onChange: (next: AgentConfig) => void; errors: string[] }) {
  const setLimits = (limits: Partial<AgentLimits>) => onChange({ ...agent, limits: { ...agent.limits, ...limits } });
  const setSubagent = (index: number, next: AgentSubagent) =>
    onChange({ ...agent, subagents: agent.subagents.map((item, i) => (i === index ? next : item)) });
  const removeSubagent = (index: number) => onChange({ ...agent, subagents: agent.subagents.filter((_, i) => i !== index) });
  const addSubagent = () => onChange({ ...agent, subagents: [...agent.subagents, emptySubagent(agent.subagents.map(s => s.id))] });

  return <div className="space-y-3">
    {errors.length > 0 && (
      <ul role="alert" className="list-disc space-y-1 rounded-xl border border-red-200 bg-red-50 px-4 py-3 pl-8 text-xs text-red-700">
        {errors.map(err => <li key={err}>{err}</li>)}
      </ul>
    )}
    {agent.subagents.length === 0 && <p className="text-sm text-amber-700">Noch keine Subagenten. Mindestens einer ist erforderlich.</p>}
    <div className="space-y-3">
      {agent.subagents.map((subagent, index) => (
        <SubagentEditor
          key={index}
          subagent={subagent}
          spaces={spaces}
          onChange={next => setSubagent(index, next)}
          onRemove={() => removeSubagent(index)}
        />
      ))}
    </div>
    <Button type="button" variant="outline" size="sm" onClick={addSubagent}><Plus size={15} />Subagent hinzufügen</Button>

    <fieldset className="rounded-xl bg-slate-50 p-3">
      <legend className="px-1 text-sm font-medium text-slate-700">Grenzen für den gesamten Agentenlauf</legend>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <Field label="Parallele Subagenten (max_parallel)"><input className={inputClass} type="number" min={1} max={20} value={agent.limits.max_parallel} onChange={event => setLimits({ max_parallel: Number(event.target.value) || 1 })} /></Field>
        <Field label="Folgerunden (max_followups)"><input className={inputClass} type="number" min={0} max={10} value={agent.limits.max_followups} onChange={event => setLimits({ max_followups: Number(event.target.value) || 0 })} /></Field>
        <Field label="Such-Budget insgesamt (budget_searches)"><input className={inputClass} type="number" min={0} max={500} value={agent.limits.budget_searches} onChange={event => setLimits({ budget_searches: Number(event.target.value) || 0 })} /></Field>
        <Field label="Zeitlimit gesamt (Sekunden)"><input className={inputClass} type="number" min={1} max={3600} value={agent.limits.timeout_seconds} onChange={event => setLimits({ timeout_seconds: Number(event.target.value) || 120 })} /></Field>
      </div>
    </fieldset>
  </div>;
}

function SubagentEditor({
  subagent, spaces, onChange, onRemove,
}: { subagent: AgentSubagent; spaces: KnowledgeSpace[]; onChange: (next: AgentSubagent) => void; onRemove: () => void }) {
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

  return <div className="space-y-3 rounded-xl border border-slate-200 p-3">
    <div className="flex items-start justify-between gap-2">
      <div className="grid flex-1 gap-3 sm:grid-cols-2">
        <Field label="Name">
          <input
            className={inputClass}
            value={subagent.name}
            onChange={event => {
              const name = event.target.value;
              onChange({ ...subagent, name, id: idTouched ? subagent.id : slugify(name) });
            }}
            placeholder="IT Support"
          />
        </Field>
        <Field label="ID (Slug)" hint="Wird aus dem Namen abgeleitet, kann angepasst werden.">
          <input
            className={inputClass}
            value={subagent.id}
            onChange={event => { setIdTouched(true); onChange({ ...subagent, id: event.target.value.toLowerCase() }); }}
            placeholder="it-support"
          />
        </Field>
      </div>
      <Button type="button" variant="ghost" size="sm" onClick={onRemove} aria-label={`${subagent.name || subagent.id} entfernen`}><Trash2 size={15} /></Button>
    </div>
    <Field label="Beschreibung"><input className={inputClass} value={subagent.description || ''} onChange={event => onChange({ ...subagent, description: event.target.value || null })} /></Field>
    <Field label="Fachlicher Auftrag"><textarea className={inputClass} rows={2} value={subagent.mission} onChange={event => onChange({ ...subagent, mission: event.target.value })} placeholder="Beantwortet IT-/Helpdesk-Fragen." /></Field>
    <ScopeChoices title="Collections" emptyLabel="Mindestens eine Collection wählen, oder unten Altbestand einbeziehen." items={spaces.map(space => space.slug)} selected={subagent.collections} onToggle={toggleCollection} />
    <Toggle checked={subagent.include_uncollected} onChange={value => onChange({ ...subagent, include_uncollected: value })} label="Dokumente ohne Wissensbereich einbeziehen" />
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Team (Filter)"><input className={inputClass} value={subagent.filters.team || ''} onChange={event => setFilter('team', event.target.value)} /></Field>
      <Field label="Abteilung (Filter)"><input className={inputClass} value={subagent.filters.department || ''} onChange={event => setFilter('department', event.target.value)} /></Field>
      <Field label="Quelle (Filter)"><input className={inputClass} value={subagent.filters.source || ''} onChange={event => setFilter('source', event.target.value)} /></Field>
      <Field label="Sprache (Filter)"><input className={inputClass} value={subagent.filters.language || ''} onChange={event => setFilter('language', event.target.value)} /></Field>
      <Field label="Dokumenttyp (Filter)"><input className={inputClass} value={subagent.filters.document_type || ''} onChange={event => setFilter('document_type', event.target.value)} /></Field>
      <Field label="Tags (Filter)" hint="Kommagetrennt.">
        <input
          className={inputClass}
          value={(subagent.filters.tags || []).join(', ')}
          onChange={event => setTagsFilter(event.target.value)}
        />
      </Field>
    </div>
    <fieldset className="rounded-xl bg-slate-50 p-3"><legend className="px-1 text-sm font-medium text-slate-700">Grenzen dieses Subagenten</legend>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        <Field label="Max. Suchen"><input className={inputClass} type="number" min={1} max={50} value={subagent.limits.max_searches} onChange={event => setLimits({ max_searches: Number(event.target.value) || 1 })} /></Field>
        <Field label="Max. Treffer/Suche"><input className={inputClass} type="number" min={1} max={50} value={subagent.limits.max_results} onChange={event => setLimits({ max_results: Number(event.target.value) || 1 })} /></Field>
        <Field label="Zeitlimit (Sekunden)"><input className={inputClass} type="number" min={1} max={3600} value={subagent.limits.timeout_seconds} onChange={event => setLimits({ timeout_seconds: Number(event.target.value) || 60 })} /></Field>
      </div>
    </fieldset>
    <details className="rounded-xl border border-slate-200 p-3">
      <summary className="cursor-pointer text-sm font-medium text-slate-700">Modell-Override (optional)</summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="Provider"><input className={inputClass} value={subagent.model?.provider || ''} onChange={event => setModel({ provider: event.target.value })} placeholder="openai" /></Field>
        <Field label="Modell"><input className={inputClass} value={subagent.model?.model || ''} onChange={event => setModel({ model: event.target.value })} placeholder="gpt-4o-mini" /></Field>
      </div>
      <Toggle checked={Boolean(subagent.model?.supports_tools)} onChange={value => setModel({ supports_tools: value })} label="Modell unterstützt Tool-Calls" />
      <Button type="button" variant="ghost" size="sm" className="mt-2" onClick={() => onChange({ ...subagent, model: null })}>Override entfernen</Button>
    </details>
  </div>;
}
