import { ReviewTasks, type ReviewTasksInitialFilters } from '@/components/portal/review-tasks';

export default async function ReviewTasksPage({
  searchParams,
}: {
  searchParams: Promise<ReviewTasksInitialFilters>;
}) {
  // ?aufgabe= opens one task (deep link, e.g. from a webhook mail); ?art=,
  // ?status= and ?bereich= preset the filters. Keyed like /documents so a
  // navigation to new params while on the page resets the state.
  const { aufgabe, art, status, bereich } = await searchParams;
  const initial = { aufgabe, art, status, bereich };
  return <ReviewTasks key={[aufgabe, art, status, bereich].map((value) => value ?? '').join('\n')} initial={initial} />;
}
