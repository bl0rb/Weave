import { ChatApp } from '@/components/chat/chat-app';

// Presence of the session cookie is already enforced by middleware.ts
// (redirects to /login when it's simply missing). Validity is enforced
// per-call by the API routes themselves — ChatApp reacts to a 401 from
// GET /api/bots by sending the browser to /login, since that's the
// earliest point an expired-but-present cookie can actually be detected.
export default function HomePage() {
  return <ChatApp />;
}
