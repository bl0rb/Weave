import { SourceForm, SOURCE_FORM_KINDS, type SourceFormKind } from '@/components/portal/source-form';
export default async function Page({ searchParams }: { searchParams: Promise<{ collection?: string; kind?: string; source?: string; catalog?: string }> }) {
  const { collection, kind, source, catalog } = await searchParams;
  const initialKind = SOURCE_FORM_KINDS.includes(kind as SourceFormKind) ? (kind as SourceFormKind) : catalog ? 'catalog' : undefined;
  return <SourceForm key={`${collection || 'new'}\n${initialKind ?? ''}\n${source ?? ''}\n${catalog ?? ''}`} initialCollection={collection} initialKind={initialKind} initialSourceId={source} initialCatalogEntryId={catalog} />;
}
