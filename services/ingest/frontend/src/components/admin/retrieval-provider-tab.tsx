'use client';

import { useEffect, useState } from 'react';
import { LoaderCircle, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { ErrorNotice, Field, LoadingState, SectionCard, errorMessage, inputClass } from './admin-shared';

type Config = {
  embedding_provider: string; embedding_base_url: string; embedding_model: string; embedding_dimension: number;
  embedding_batch_size: number; embedding_has_api_key: boolean; rerank_provider: string; rerank_base_url: string;
  rerank_model: string; rerank_max_documents: number; rerank_batch_size: number; rerank_threads: number;
  rerank_has_api_key: boolean; semantic_weight: number; lexical_weight: number; updated_at: string | null;
};
const PATH = '/api/v1/auth/admin/retrieval-provider';

export function RetrievalProviderTab() {
  const [config, setConfig] = useState<Config | null>(null);
  const [embeddingKey, setEmbeddingKey] = useState('');
  const [rerankKey, setRerankKey] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => { apiJson<Config>(PATH).then(setConfig).catch(err => setError(errorMessage(err))); }, []);
  if (!config) return <SectionCard title="Suche und Modelle" description="Embedding, hybride Suche und optionales Reranking zentral verwalten."><ErrorNotice message={error} /><LoadingState label="Suchkonfiguration wird geladen…" /></SectionCard>;
  const set = (patch: Partial<Config>) => setConfig({ ...config, ...patch });
  const save = async () => {
    setSaving(true); setError(null); setMessage('');
    try {
      const next = await apiJson<Config>(PATH, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...config, embedding_api_key: embeddingKey || null, rerank_api_key: rerankKey || null }) });
      setConfig(next); setEmbeddingKey(''); setRerankKey(''); setMessage('Suchkonfiguration gespeichert.');
    } catch (err) { setError(errorMessage(err)); } finally { setSaving(false); }
  };
  const semanticPercent = Math.round(config.semantic_weight * 100);
  return <SectionCard title="Suche und Modelle" description="Embedding, hybride Suche und optionales Reranking zentral verwalten.">
    <ErrorNotice message={error} />
    <div className="space-y-6">
      <div><h3 className="font-semibold text-slate-950">Hybride Suche</h3><p className="mt-1 text-sm text-slate-500">Steuert die Gewichtung der semantischen Vektorsuche gegenüber der lexikalischen Volltextsuche.</p><label className="mt-4 block text-sm font-medium text-slate-700">Semantisch {semanticPercent}% <input className="mt-2 w-full accent-emerald-700" type="range" min="0" max="100" value={semanticPercent} onChange={e => { const value = Number(e.target.value) / 100; set({ semantic_weight: value, lexical_weight: 1 - value }); }} /></label><div className="mt-1 flex justify-between text-xs text-slate-500"><span>Lexikalisch {Math.round(config.lexical_weight * 100)}%</span><span>Semantisch {semanticPercent}%</span></div></div>
      <div className="grid gap-4 lg:grid-cols-2"><Field label="Embedding-Provider"><select className={inputClass} value={config.embedding_provider} onChange={e => set({ embedding_provider: e.target.value })}><option value="fake">Fake</option><option value="openai">OpenAI-kompatibel</option></select></Field><Field label="Embedding-Modell"><input className={inputClass} value={config.embedding_model} onChange={e => set({ embedding_model: e.target.value })} /></Field><Field label="Embedding-Endpoint"><input className={inputClass} type="url" value={config.embedding_base_url} onChange={e => set({ embedding_base_url: e.target.value })} placeholder="http://weave-embeddings:8000" /></Field><Field label="Dimension"><input className={inputClass} type="number" min={1} value={config.embedding_dimension} onChange={e => set({ embedding_dimension: Number(e.target.value) })} /></Field><Field label="Embedding API-Key"><input className={inputClass} type="password" value={embeddingKey} onChange={e => setEmbeddingKey(e.target.value)} placeholder={config.embedding_has_api_key ? 'Gespeicherten Key beibehalten' : 'Optional'} /></Field></div>
      <div className="grid gap-4 lg:grid-cols-2"><Field label="Reranker"><select className={inputClass} value={config.rerank_provider} onChange={e => set({ rerank_provider: e.target.value })}><option value="none">Aus</option><option value="api">API / selbst gehostet</option><option value="fake">Fake</option></select></Field><Field label="Reranker-Modell"><input className={inputClass} value={config.rerank_model} onChange={e => set({ rerank_model: e.target.value })} disabled={config.rerank_provider === 'none'} /></Field><Field label="Reranker-Endpoint"><input className={inputClass} type="url" value={config.rerank_base_url} onChange={e => set({ rerank_base_url: e.target.value })} disabled={config.rerank_provider === 'none'} placeholder="http://weave-reranker:8000" /></Field><Field label="Reranker API-Key"><input className={inputClass} type="password" value={rerankKey} onChange={e => setRerankKey(e.target.value)} disabled={config.rerank_provider === 'none'} placeholder={config.rerank_has_api_key ? 'Gespeicherten Key beibehalten' : 'Optional'} /></Field><Field label="Maximale Kandidaten"><input className={inputClass} type="number" min={1} value={config.rerank_max_documents} onChange={e => set({ rerank_max_documents: Number(e.target.value) })} disabled={config.rerank_provider === 'none'} /></Field></div>
      <div className="flex items-center gap-3 border-t border-slate-100 pt-4"><Button onClick={save} disabled={saving}>{saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}Speichern</Button><span role="status" className="text-sm text-slate-600">{message}</span></div>
      <p className="text-xs text-slate-500">API-Keys werden verschlüsselt gespeichert. Eine Änderung von Embedding-Modell oder Dimension erfordert einen vollständigen Reindex; der Reranker kann jederzeit auf „Aus“ gestellt werden.</p>
    </div>
  </SectionCard>;
}
