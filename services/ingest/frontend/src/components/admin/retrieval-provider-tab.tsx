'use client';

import { useEffect, useState } from 'react';
import { Database, LoaderCircle, RefreshCw, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { useVisiblePolling } from '@/lib/data-cache';
import { ConfirmDialog, ErrorNotice, Field, LoadingState, SectionCard, errorMessage, inputClass } from './admin-shared';

type Config = {
  embedding_provider: string; embedding_base_url: string; embedding_model: string; embedding_dimension: number;
  embedding_batch_size: number; embedding_has_api_key: boolean; embedding_key_source: string; rerank_provider: string; rerank_base_url: string;
  rerank_model: string; rerank_max_documents: number; rerank_batch_size: number; rerank_threads: number;
  rerank_has_api_key: boolean; rerank_key_source: string; semantic_weight: number; lexical_weight: number; updated_at: string | null;
  reindex_started?: boolean;
};
const PATH = '/api/v1/auth/admin/retrieval-provider';

export function RetrievalProviderTab() {
  const [config, setConfig] = useState<Config | null>(null);
  const [initialEmbedding, setInitialEmbedding] = useState<Pick<Config, 'embedding_provider' | 'embedding_base_url' | 'embedding_model' | 'embedding_dimension' | 'embedding_batch_size'> | null>(null);
  const [embeddingKey, setEmbeddingKey] = useState('');
  const [rerankKey, setRerankKey] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => { apiJson<Config>(PATH).then(value => { setConfig(value); setInitialEmbedding(value); }).catch(err => setError(errorMessage(err))); }, []);
  if (!config) return <div className="space-y-6"><SectionCard title="Suche und Modelle" description="Embedding, hybride Suche und optionales Reranking zentral verwalten."><ErrorNotice message={error} /><LoadingState label="Suchkonfiguration wird geladen…" /></SectionCard></div>;
  const set = (patch: Partial<Config>) => setConfig({ ...config, ...patch });
  const save = async () => {
    setSaving(true); setError(null); setMessage('');
    try {
      const embeddingChanged = initialEmbedding !== null && (
        initialEmbedding.embedding_provider !== config.embedding_provider ||
        initialEmbedding.embedding_base_url !== config.embedding_base_url ||
        initialEmbedding.embedding_model !== config.embedding_model ||
        initialEmbedding.embedding_dimension !== config.embedding_dimension ||
        initialEmbedding.embedding_batch_size !== config.embedding_batch_size
      );
      const confirmed = embeddingChanged ? window.confirm('Embedding-Einstellungen geändert. Alle bestehenden Dokumente werden neu eingebettet. Fortfahren?') : true;
      if (!confirmed) return;
      const next = await apiJson<Config>(PATH, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        embedding_provider: config.embedding_provider,
        embedding_base_url: config.embedding_base_url,
        embedding_model: config.embedding_model,
        embedding_dimension: config.embedding_dimension,
        embedding_batch_size: config.embedding_batch_size,
        embedding_api_key: embeddingKey || null,
        rerank_provider: config.rerank_provider,
        rerank_base_url: config.rerank_base_url,
        rerank_model: config.rerank_model,
        rerank_max_documents: config.rerank_max_documents,
        rerank_batch_size: config.rerank_batch_size,
        rerank_threads: config.rerank_threads,
        rerank_api_key: rerankKey || null,
        semantic_weight: config.semantic_weight,
        lexical_weight: config.lexical_weight,
        confirm_reindex: confirmed,
      }) });
      setConfig(next); setInitialEmbedding(next); setEmbeddingKey(''); setRerankKey(''); setMessage(next.reindex_started ? 'Gespeichert. Vollständige Reindizierung wurde gestartet.' : 'Suchkonfiguration gespeichert.');
    } catch (err) { setError(errorMessage(err)); } finally { setSaving(false); }
  };
  const semanticPercent = Math.round(config.semantic_weight * 100);
  return <div className="space-y-6">
  <SectionCard title="Suche und Modelle" description="Embedding, hybride Suche und optionales Reranking zentral verwalten.">
    <ErrorNotice message={error} />
    <div className="space-y-6">
      <div><h3 className="font-semibold text-slate-950">Hybride Suche</h3><p className="mt-1 text-sm text-slate-500">Steuert die Gewichtung der semantischen Vektorsuche gegenüber der lexikalischen Volltextsuche.</p><label className="mt-4 block text-sm font-medium text-slate-700">Semantisch {semanticPercent}% <input className="mt-2 w-full accent-emerald-700" type="range" min="0" max="100" value={semanticPercent} onChange={e => { const value = Number(e.target.value) / 100; set({ semantic_weight: value, lexical_weight: 1 - value }); }} /></label><div className="mt-1 flex justify-between text-xs text-slate-500"><span>Lexikalisch {Math.round(config.lexical_weight * 100)}%</span><span>Semantisch {semanticPercent}%</span></div></div>
      <div className="grid gap-4 lg:grid-cols-2"><Field label="Embedding-Provider"><select className={inputClass} value={config.embedding_provider} onChange={e => set({ embedding_provider: e.target.value })}><option value="fake">Fake</option><option value="openai">OpenAI-kompatibel</option></select></Field><Field label="Embedding-Modell"><input className={inputClass} value={config.embedding_model} onChange={e => set({ embedding_model: e.target.value })} /></Field><Field label="Embedding-Endpoint"><input className={inputClass} type="url" value={config.embedding_base_url} onChange={e => set({ embedding_base_url: e.target.value })} placeholder="http://weave-embeddings:8000" /></Field><Field label="Dimension"><input className={inputClass} type="number" min={1} value={config.embedding_dimension} onChange={e => set({ embedding_dimension: Number(e.target.value) })} /></Field><Field label={`Embedding API-Key (${config.embedding_key_source})`}><input className={inputClass} type="password" value={embeddingKey} onChange={e => setEmbeddingKey(e.target.value)} placeholder={config.embedding_has_api_key ? 'Gespeicherten Key beibehalten' : 'Optional'} /></Field></div>
      <div className="grid gap-4 lg:grid-cols-2"><Field label="Reranker"><select className={inputClass} value={config.rerank_provider} onChange={e => set({ rerank_provider: e.target.value })}><option value="none">Aus</option><option value="api">API / selbst gehostet</option><option value="fake">Fake</option></select></Field><Field label="Reranker-Modell"><input className={inputClass} value={config.rerank_model} onChange={e => set({ rerank_model: e.target.value })} disabled={config.rerank_provider === 'none'} /></Field><Field label="Reranker-Endpoint"><input className={inputClass} type="url" value={config.rerank_base_url} onChange={e => set({ rerank_base_url: e.target.value })} disabled={config.rerank_provider === 'none'} placeholder="http://weave-reranker:8000" /></Field><Field label={`Reranker API-Key (${config.rerank_key_source})`}><input className={inputClass} type="password" value={rerankKey} onChange={e => setRerankKey(e.target.value)} disabled={config.rerank_provider === 'none'} placeholder={config.rerank_has_api_key ? 'Gespeicherten Key beibehalten' : 'Optional'} /></Field><Field label="Maximale Kandidaten"><input className={inputClass} type="number" min={1} value={config.rerank_max_documents} onChange={e => set({ rerank_max_documents: Number(e.target.value) })} disabled={config.rerank_provider === 'none'} /></Field></div>
      <div className="flex items-center gap-3 border-t border-slate-100 pt-4"><Button onClick={save} disabled={saving}>{saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}Speichern</Button><span role="status" className="text-sm text-slate-600">{message}</span></div>
      <p className="text-xs text-slate-500">API-Keys werden verschlüsselt gespeichert. Eine Änderung von Provider oder Modell mit gleicher Dimension startet nach Bestätigung eine vollständige Reindizierung. Eine Dimensionsänderung benötigt zusätzlich eine Datenbankmigration; der Reranker kann jederzeit auf „Aus“ gestellt werden.</p>
    </div>
  </SectionCard>
  <IndexMaintenanceSection />
  </div>;
}

// --- Index-Wartung --------------------------------------------------------
//
// Two explicit, rarely-used admin actions, each far more expensive than a
// normal config save (a full re-embed of every document, or a full
// re-delivery of every release) -- kept as its own SectionCard below the
// config form rather than folded into `save()`, and each gated by its own
// ConfirmDialog rather than window.confirm so the German cost explanation
// has room to be specific.

const REINDEX_STATUS_PATH = '/api/v1/admin/retrieval-provider/reindex-status';
const REBUILD_STATUS_PATH = '/api/v1/admin/knowledge/rebuild-status';
const STATUS_POLL_MS = 3000;
// Neither action reports its own completion (Knowledge's reindex task has
// no separate progress row, and worker-driven release delivery has no
// "done" signal either) -- polling just runs for a bounded window after the
// action starts, long enough to see the numbers move, then stops on its own
// so the button doesn't stay disabled forever.
const STATUS_POLL_WINDOW_MS = 120_000;

type ReindexStatus = { total: number; by_status: Record<string, number>; newest_updated_at: string | null };
type RebuildStatus = {
  total_releases: number; withdrawn: number; pending: number; sent: number; failed: number; last_sent_at: string | null;
};

export function IndexMaintenanceSection() {
  const [confirming, setConfirming] = useState<'reindex' | 'rebuild' | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeError, setNoticeError] = useState<string | null>(null);

  const [reindexBusy, setReindexBusy] = useState(false);
  const [reindexStatus, setReindexStatus] = useState<ReindexStatus | null>(null);
  const [reindexStatusError, setReindexStatusError] = useState<string | null>(null);

  const [rebuildBusy, setRebuildBusy] = useState(false);
  const [rebuildStatus, setRebuildStatus] = useState<RebuildStatus | null>(null);
  const [rebuildStatusError, setRebuildStatusError] = useState<string | null>(null);

  useVisiblePolling(() => {
    apiJson<ReindexStatus>(REINDEX_STATUS_PATH).then(setReindexStatus).catch(err => setReindexStatusError(errorMessage(err)));
  }, reindexBusy ? STATUS_POLL_MS : null);

  useVisiblePolling(() => {
    apiJson<RebuildStatus>(REBUILD_STATUS_PATH).then(setRebuildStatus).catch(err => setRebuildStatusError(errorMessage(err)));
  }, rebuildBusy ? STATUS_POLL_MS : null);

  useEffect(() => {
    if (!reindexBusy) return;
    const timer = setTimeout(() => setReindexBusy(false), STATUS_POLL_WINDOW_MS);
    return () => clearTimeout(timer);
  }, [reindexBusy]);

  useEffect(() => {
    if (!rebuildBusy) return;
    const timer = setTimeout(() => setRebuildBusy(false), STATUS_POLL_WINDOW_MS);
    return () => clearTimeout(timer);
  }, [rebuildBusy]);

  const startReindex = async () => {
    setNotice(null); setNoticeError(null);
    try {
      const result = await apiJson<{ started: boolean; task_id: string | null }>('/api/v1/admin/retrieval-provider/reindex', { method: 'POST' });
      setNotice(result.task_id ? `Neuberechnung gestartet (Task ${result.task_id}).` : 'Neuberechnung gestartet.');
      setReindexBusy(true);
      setReindexStatusError(null);
    } catch (err) {
      setNoticeError(errorMessage(err));
      throw err;
    }
  };

  const startRebuild = async () => {
    setNotice(null); setNoticeError(null);
    try {
      const result = await apiJson<{ requeued: number; worker_required: boolean }>('/api/v1/admin/knowledge/rebuild', { method: 'POST' });
      setNotice(`${result.requeued} Veröffentlichung(en) zur Neuauslieferung eingereiht. Ein laufender Ingest-Worker liefert sie aus.`);
      setRebuildBusy(true);
      setRebuildStatusError(null);
    } catch (err) {
      setNoticeError(errorMessage(err));
      throw err;
    }
  };

  return <SectionCard title="Index-Wartung" description="Manuelle Eingriffe in den Wissensindex — beide Aktionen sind aufwendig und normalerweise nicht nötig.">
    <ErrorNotice message={noticeError} />
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-xl border border-slate-200 p-4">
        <h3 className="font-semibold text-slate-950">Vektoren neu berechnen</h3>
        <p className="mt-1 text-sm text-slate-500">Bettet alle indizierten Dokumente mit dem aktuellen Embedding-Modell neu ein.</p>
        <Button className="mt-3" variant="outline" onClick={() => setConfirming('reindex')} disabled={reindexBusy}>
          {reindexBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          {reindexBusy ? 'Läuft…' : 'Vektoren neu berechnen'}
        </Button>
        {reindexStatusError && <p className="mt-2 text-xs text-red-600">{reindexStatusError}</p>}
        {reindexStatus && (
          <p className="mt-2 text-xs text-slate-500">
            {reindexStatus.total} Dokument(e) gesamt — {Object.entries(reindexStatus.by_status).map(([status, count]) => `${status}: ${count}`).join(', ')}
          </p>
        )}
      </div>
      <div className="rounded-xl border border-slate-200 p-4">
        <h3 className="font-semibold text-slate-950">Index aus Freigaben neu aufbauen</h3>
        <p className="mt-1 text-sm text-slate-500">Liefert alle freigegebenen Dokumente erneut an den Wissensdienst aus — dafür ist ein laufender Ingest-Worker nötig.</p>
        <Button className="mt-3" variant="outline" onClick={() => setConfirming('rebuild')} disabled={rebuildBusy}>
          {rebuildBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
          {rebuildBusy ? 'Läuft…' : 'Index aus Freigaben neu aufbauen'}
        </Button>
        {rebuildStatusError && <p className="mt-2 text-xs text-red-600">{rebuildStatusError}</p>}
        {rebuildStatus && (
          <p className="mt-2 text-xs text-slate-500">
            {rebuildStatus.total_releases} Freigabe(n) — offen: {rebuildStatus.pending}, ausgeliefert: {rebuildStatus.sent}, fehlgeschlagen: {rebuildStatus.failed}, zurückgezogen: {rebuildStatus.withdrawn}
          </p>
        )}
      </div>
    </div>
    {notice && <p role="status" className="mt-4 text-sm text-slate-600">{notice}</p>}

    {confirming === 'reindex' && (
      <ConfirmDialog
        title="Vektoren neu berechnen"
        body={<p>Bettet <strong>alle</strong> indizierten Dokumente mit dem aktuell konfigurierten Embedding-Modell neu ein. Das kann je nach Dokumentmenge lange dauern und erzeugt entsprechende Last auf dem Embedding-Dienst. Fortfahren?</p>}
        confirmLabel="Neu berechnen"
        onClose={() => setConfirming(null)}
        onConfirm={async () => { await startReindex(); setConfirming(null); }}
      />
    )}
    {confirming === 'rebuild' && (
      <ConfirmDialog
        title="Index aus Freigaben neu aufbauen"
        body={<p>Reiht <strong>alle</strong> nicht zurückgezogenen Freigaben erneut zur Auslieferung an den Wissensdienst ein. Dafür muss ein Ingest-Worker laufen — ohne laufenden Worker bleiben die Freigaben nur in der Warteschlange. Fortfahren?</p>}
        confirmLabel="Neu aufbauen"
        onClose={() => setConfirming(null)}
        onConfirm={async () => { await startRebuild(); setConfirming(null); }}
      />
    )}
  </SectionCard>;
}
