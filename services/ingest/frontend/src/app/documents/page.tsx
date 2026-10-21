import { PortalDocuments, type DocumentsInitialFilters } from '@/components/portal/documents';

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<DocumentsInitialFilters>;
}) {
  const { q, stand, bereich, bewertung, quelle, ansicht } = await searchParams;
  // Keyed on the params so a navigation to new ones while already on
  // /documents (the topbar or Übersicht search) resets the filters; the
  // component's own URL sync uses history.replaceState and never changes these props.
  return <PortalDocuments key={[q, stand, bereich, bewertung, quelle, ansicht].map((value) => value ?? '').join('\n')} initial={{ q, stand, bereich, bewertung, quelle, ansicht }} />;
}
