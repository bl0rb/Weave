import { PortalDocuments } from '@/components/portal/documents';

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; stand?: string; bereich?: string }>;
}) {
  const { q, stand, bereich } = await searchParams;
  return <PortalDocuments initialQuery={q} initialStand={stand} initialBereich={bereich} />;
}
