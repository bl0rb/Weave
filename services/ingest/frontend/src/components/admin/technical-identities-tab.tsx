'use client';

import { useState, type FormEvent } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, KeyRound, Plus, RefreshCw, ShieldOff } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import type { KnowledgeSpace } from '@/lib/portal';
import {
  Badge,
  ConfirmDialog,
  ErrorNotice,
  Field,
  LoadingState,
  Modal,
  SectionCard,
  apiSend,
  errorMessage,
  inputClass,
  useAdminList,
} from './admin-shared';

type TechnicalIdentity = {
  id: string;
  name: string;
  description: string | null;
  allowed_collections: string[];
  enabled: boolean;
  token_prefix: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_by: string | null;
};

type TechnicalIdentityCreateResponse = TechnicalIdentity & { token: string };

type AuditEntry = { id: string; event: string; actor: string | null; details: Record<string, unknown>; created_at: string };

const BASE_PATH = '/api/v1/auth/admin/technical-identities';

function statusOf(item: TechnicalIdentity): { label: string; tone: 'emerald' | 'slate' | 'amber' } {
  if (item.revoked_at) return { label: 'Widerrufen', tone: 'slate' };
  if (item.expires_at && new Date(item.expires_at).getTime() <= Date.now()) return { label: 'Abgelaufen', tone: 'amber' };
  if (!item.enabled) return { label: 'Deaktiviert', tone: 'slate' };
  return { label: 'Aktiv', tone: 'emerald' };
}

function formatDate(value: string | null): string {
  if (!value) return '–';
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
}

export function TechnicalIdentitiesTab() {
  const identities = useAdminList<TechnicalIdentity>(BASE_PATH);
  const spaces = useAdminList<KnowledgeSpace>('/api/v1/collections');
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<TechnicalIdentity | null>(null);
  const [rotating, setRotating] = useState<TechnicalIdentity | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [issuedToken, setIssuedToken] = useState<TechnicalIdentityCreateResponse | null>(null);
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const loading = identities.loading || spaces.loading;
  const error = identities.error || spaces.error;

  async function rotate(item: TechnicalIdentity) {
    setActionError(null);
    try {
      const rotated = await apiJson<TechnicalIdentityCreateResponse>(`${BASE_PATH}/${encodeURIComponent(item.id)}/rotate`, {
        method: 'POST',
      });
      setIssuedToken(rotated);
      setNotice('');
      await identities.reload();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }

  return <div className="space-y-6">
    <SectionCard
      title="Technische Identitäten"
      description="Eigenständige Integrationen und externe KI-Agenten (MCP) erhalten hier ein eigenes Token mit explizit gewährten Wissensbereichen — nie den Zugriff eines Nutzers oder Teams."
      actions={<Button variant="outline" size="sm" onClick={() => { setCreating(true); setNotice(''); }}><Plus size={15} />Identität anlegen</Button>}
    >
      <ErrorNotice message={error} />
      <ErrorNotice message={actionError} />
      {notice && <p role="status" className="mb-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}
      {loading ? <LoadingState label="Technische Identitäten werden geladen…" /> : identities.items.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <KeyRound className="h-9 w-9 text-slate-300" />
          <div><p className="font-medium text-slate-800">Noch keine technischen Identitäten</p><p className="mt-1 text-sm text-slate-500">Ohne eine angelegte Identität hat keine externe Integration Zugriff auf Weave-Tools.</p></div>
          <Button variant="outline" size="sm" onClick={() => setCreating(true)}><Plus size={15} />Erste Identität anlegen</Button>
        </div>
      ) : <ul className="divide-y divide-slate-100">
        {identities.items.map(item => {
          const status = statusOf(item);
          const isExpanded = expanded === item.id;
          return <li key={item.id} className="py-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <KeyRound size={18} className="text-emerald-700" />
                  <h3 className="font-semibold text-slate-950">{item.name}</h3>
                  <Badge tone={status.tone}>{status.label}</Badge>
                  <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">{item.token_prefix}…</code>
                </div>
                {item.description && <p className="mt-1 text-sm text-slate-600">{item.description}</p>}
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
                  <span>Wissensbereiche: {item.allowed_collections.length ? item.allowed_collections.join(', ') : 'keine'}</span>
                  <span>Letzte Nutzung: {formatDate(item.last_used_at)}</span>
                  {item.expires_at && <span>Läuft ab: {formatDate(item.expires_at)}</span>}
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                <Button variant="ghost" size="sm" onClick={() => setRotating(item)} aria-label={`${item.name} rotieren`}><RefreshCw size={15} />Token erneuern</Button>
                {!item.revoked_at && <Button variant="ghost" size="sm" onClick={() => setRevoking(item)} aria-label={`${item.name} widerrufen`}><ShieldOff size={15} />Widerrufen</Button>}
                <Button variant="ghost" size="sm" onClick={() => setExpanded(isExpanded ? null : item.id)} aria-label={`Audit-Verlauf von ${item.name} ${isExpanded ? 'einklappen' : 'anzeigen'}`}>
                  {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}Audit
                </Button>
              </div>
            </div>
            {isExpanded && <AuditList identityId={item.id} />}
          </li>;
        })}
      </ul>}
    </SectionCard>

    {creating && <CreateDialog
      spaces={spaces.items}
      onClose={() => setCreating(false)}
      onCreated={async created => {
        setCreating(false);
        setIssuedToken(created);
        await identities.reload();
      }}
    />}

    {issuedToken && <TokenDisplay identity={issuedToken} onClose={() => { setIssuedToken(null); setNotice('Token gespeichert und angezeigt.'); }} />}

    {revoking && <ConfirmDialog
      title="Identität widerrufen"
      body={<p>Die technische Identität <strong className="text-slate-950">{revoking.name}</strong> widerrufen? Ihr Token funktioniert danach unwiderruflich nicht mehr.</p>}
      confirmLabel="Identität widerrufen"
      onClose={() => setRevoking(null)}
      onConfirm={async () => {
        await apiSend(`${BASE_PATH}/${encodeURIComponent(revoking.id)}/revoke`, { method: 'POST' });
        setRevoking(null);
        setNotice('Identität widerrufen.');
        await identities.reload();
      }}
    />}

    {rotating && <ConfirmDialog
      title="Token erneuern"
      body={<p>Token der technischen Identität <strong className="text-slate-950">{rotating.name}</strong> erneuern? Das aktuelle Token wird sofort unwiderruflich ungültig — jede Integration, die es noch verwendet, verliert damit den Zugriff.</p>}
      confirmLabel="Token erneuern"
      onClose={() => setRotating(null)}
      onConfirm={async () => {
        await rotate(rotating);
        setRotating(null);
      }}
    />}
  </div>;
}

function CreateDialog({ spaces, onClose, onCreated }: {
  spaces: KnowledgeSpace[];
  onClose: () => void;
  onCreated: (created: TechnicalIdentityCreateResponse) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [collections, setCollections] = useState<string[]>([]);
  const [expiresInDays, setExpiresInDays] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSave = Boolean(name.trim() && !saving);

  const toggle = (slug: string) => setCollections(current => current.includes(slug) ? current.filter(item => item !== slug) : [...current, slug]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true); setError(null);
    try {
      const created = await apiJson<TechnicalIdentityCreateResponse>(BASE_PATH, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          description: description || null,
          allowed_collections: collections,
          expires_in_days: expiresInDays.trim() ? Number(expiresInDays) : null,
        }),
      });
      await onCreated(created);
    } catch (err) { setError(errorMessage(err)); setSaving(false); }
  }

  return <Modal title="Technische Identität anlegen" onClose={onClose}>
    <ErrorNotice message={error} />
    <form className="space-y-5" onSubmit={save}>
      <Field label="Name" hint="Erkennbarer Name der Integration, z. B. der n8n-Flow oder der MCP-Client."><input className={inputClass} required value={name} onChange={event => setName(event.target.value)} placeholder="n8n-Integration" /></Field>
      <Field label="Beschreibung"><textarea className={inputClass} rows={2} value={description} onChange={event => setDescription(event.target.value)} /></Field>
      <Field label="Ablauf (Tage)" hint="Leer lassen für ein Token ohne Ablaufdatum."><input className={inputClass} type="number" min={1} max={3650} value={expiresInDays} onChange={event => setExpiresInDays(event.target.value)} placeholder="z. B. 365" /></Field>
      <fieldset>
        <legend className="text-sm font-medium text-slate-700">Wissensbereiche</legend>
        <p className="mt-1 text-xs text-slate-400">Keine Auswahl: die Identität erhält KEINEN Wissenszugriff, bis hier Wissensbereiche gewährt werden.</p>
        {spaces.length ? <div className="mt-2 grid max-h-36 gap-2 overflow-y-auto rounded-xl border border-slate-200 p-3 sm:grid-cols-2">
          {spaces.map(space => <label key={space.slug} className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={collections.includes(space.slug)} onChange={() => toggle(space.slug)} />{space.slug}
          </label>)}
        </div> : <p className="mt-2 text-sm text-amber-700">Noch keine Wissensbereiche verfügbar.</p>}
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-4">
        <Button type="button" variant="outline" onClick={onClose} disabled={saving}>Abbrechen</Button>
        <Button type="submit" disabled={!canSave}>{saving ? 'Wird angelegt…' : 'Identität speichern'}</Button>
      </div>
    </form>
  </Modal>;
}

function TokenDisplay({ identity, onClose }: { identity: TechnicalIdentityCreateResponse; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(identity.token);
      setCopied(true);
    } catch {
      // Clipboard API unavailable — the token stays visible/selectable.
    }
  }

  return <Modal title={`Token für „${identity.name}"`} onClose={onClose}>
    <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-4">
      <p className="text-sm font-semibold text-emerald-900">Dieses Token wird nur jetzt angezeigt — jetzt sichern.</p>
      <div className="mt-2 flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded-lg border border-emerald-200 bg-white px-3 py-2 text-xs text-slate-950">{identity.token}</code>
        <Button type="button" size="sm" variant="outline" onClick={copy}>
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? 'Kopiert' : 'Kopieren'}
        </Button>
      </div>
      <p className="mt-2 text-xs font-medium text-emerald-800">Nach dem Schließen dieses Dialogs ist der volle Wert nicht mehr abrufbar — nur noch der Präfix {identity.token_prefix}… in der Liste.</p>
    </div>
    <div className="mt-4 flex justify-end"><Button type="button" onClick={onClose}>Verstanden</Button></div>
  </Modal>;
}

function AuditList({ identityId }: { identityId: string }) {
  const audit = useAdminList<AuditEntry>(`${BASE_PATH}/${encodeURIComponent(identityId)}/audit`);
  if (audit.loading) return <p className="mt-2 pl-6 text-xs text-slate-400">Audit-Verlauf wird geladen…</p>;
  if (!audit.items.length) return <p className="mt-2 pl-6 text-xs text-slate-400">Noch keine Audit-Einträge.</p>;
  return <ul className="mt-3 space-y-1 border-t border-slate-100 pl-6 pt-3 text-xs text-slate-500">
    {audit.items.map(entry => <li key={entry.id}>
      <span className="font-medium text-slate-700">{entry.event}</span>
      {entry.actor && <span> · {entry.actor}</span>}
      <span> · {formatDate(entry.created_at)}</span>
    </li>)}
  </ul>;
}
