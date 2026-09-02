import { ReviewDocument } from '@/components/portal/reviews';
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReviewDocument key={id} id={id} />;
}
