import { DocumentBrowser } from '@/components/document-browser';
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
    <main className="min-h-screen">
      <div className="mx-auto w-full max-w-screen-2xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-3xl font-semibold text-slate-950">{t('portal.chrome.breadcrumb.jobs')}</h1>
        </div>
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
      </div>
    </main>
  );
}
