/**
 * Validates and normalizes the configured chat URL (WEAVE_CHAT_PUBLIC_URL).
 * Shared by app/chat/page.tsx (server-rendered) and runtime-env.js/route.ts
 * (which exposes the same value to client components — see api-base.ts's
 * `resolveChatPublicUrl`) so both reject the same malformed configurations
 * (wrong protocol, embedded credentials) the same way.
 */
export function resolveChatUrl(value: string | undefined | null): string | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;

  try {
    const url = new URL(trimmed);
    if ((url.protocol !== 'http:' && url.protocol !== 'https:') || url.username || url.password) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}
