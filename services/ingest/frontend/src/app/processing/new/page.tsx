import { redirect } from 'next/navigation';

// The legacy four-step upload wizard was merged into "Quelle hinzufügen".
export default function NewProcessingTaskPage() {
  redirect('/sources/new');
}
