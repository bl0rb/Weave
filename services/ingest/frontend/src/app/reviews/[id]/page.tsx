import { ReviewDocument } from '@/components/portal/reviews';
export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ edit?: string | string[] }> }) {
  const { id } = await params;
  const { edit } = await searchParams;
  return <ReviewDocument key={id} id={id} initialEdit={edit === '1'} />;
}
