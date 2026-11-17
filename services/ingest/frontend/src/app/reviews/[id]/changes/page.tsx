import { DocumentChanges } from '@/components/portal/document-changes';

export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ base?: string | string[] }> }) {
  const { id } = await params;
  const { base } = await searchParams;
  const baseId = typeof base === 'string' && base ? base : null;
  // Keyed like /reviews/[id], so picking other versions starts from a fresh state.
  return <DocumentChanges key={`${id}\n${baseId ?? ''}`} id={id} base={baseId} />;
}
