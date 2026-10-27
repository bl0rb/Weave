import { SourceForm, type SourceFormKind } from '@/components/portal/source-form';

// Kept here, not imported from the 'use client' module: a server component
// only receives function proxies for a client module's exports.
const KINDS: readonly SourceFormKind[] = ['files', 'confluence', 'website', 'catalog'];

export default async function Page({ searchParams }: { searchParams: Promise<{ collection?: string; kind?: string; source?: string; catalog?: string }> }) {
  const { collection, kind, source, catalog } = await searchParams;
  const initialKind = KINDS.includes(kind as SourceFormKind) ? (kind as SourceFormKind) : catalog ? 'catalog' : undefined;
  return <SourceForm key={`${collection || 'new'}\n${initialKind ?? ''}\n${source ?? ''}\n${catalog ?? ''}`} initialCollection={collection} initialKind={initialKind} initialSourceId={source} initialCatalogEntryId={catalog} />;
}
