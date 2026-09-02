import { SourceForm } from '@/components/portal/source-form';
export default async function Page({ searchParams }: { searchParams: Promise<{ collection?: string }> }) {
  const { collection } = await searchParams;
  return <SourceForm key={collection || 'new'} initialCollection={collection} />;
}
