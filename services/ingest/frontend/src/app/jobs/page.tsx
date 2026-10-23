import { DocumentBrowser } from '@/components/document-browser';
import { DocumentTabs } from '@/components/portal/page-tabs';
import { PortalPage } from '@/components/portal/shared';
import { getTranslator } from '@/i18n/server';

export default async function JobsPage({
  searchParams,
}: {
  searchParams: Promise<{ folder?: string | string[]; type?: string | string[] }>;
}) {
  const { folder, type } = await searchParams;
  const initialFolder = Array.isArray(folder) ? folder[0] : folder;
  const initialType = Array.isArray(type) ? type[0] : type;
  const { t } = await getTranslator();

  return (
    <PortalPage title={t('portal.chrome.breadcrumb.jobs')} tabs={<DocumentTabs />}>
      <DocumentBrowser
        // initialFolder/initialType only feed the state initializers; the
        // key remounts the browser when either ?folder or ?type deep link
        // changes (back/forward or same-route navigation are
        // searchParams-only updates that would otherwise leave the
        // already-mounted component's filters unchanged).
        key={`${initialFolder ?? 'all'}:${initialType ?? 'all'}`}
        endpoint="jobs"
        allowDelete
        includeDateFilters
        initialFolder={initialFolder}
        initialType={initialType}
      />
    </PortalPage>
  );
}
