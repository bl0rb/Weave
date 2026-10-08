import { SourceForm } from '@/components/portal/source-form';
export default async function Page({ searchParams }: { searchParams: Promise<{ collection?: string; kind?: string; source?: string }> }) {
  const { collection, kind, source } = await searchParams;
  const initialKind = kind === 'confluence' || kind === 'files' ? kind : undefined;
  return <SourceForm key={`${collection || 'new'}\n${initialKind ?? ''}\n${source ?? ''}`} initialCollection={collection} initialKind={initialKind} initialSourceId={source} />;
}
