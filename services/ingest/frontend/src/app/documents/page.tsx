import { PortalDocuments } from '@/components/portal/documents';

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; stand?: string; bereich?: string }>;
}) {
  const { q, stand, bereich } = await searchParams;
  // Keyed on the params so a navigation to new ones while already on
  // /documents (the topbar search) resets the filters; the component's own
  // URL sync uses history.replaceState and never changes these props.
  return <PortalDocuments key={`${q ?? ''}\n${stand ?? ''}\n${bereich ?? ''}`} initialQuery={q} initialStand={stand} initialBereich={bereich} />;
}
