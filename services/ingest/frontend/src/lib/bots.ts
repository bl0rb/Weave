import { apiJson } from '@/lib/api';
import { jsonBody } from '@/lib/portal';

/** Roles on a managed bot (ADR 0008): owners maintain it, users chat with it. */
export type BotRole = 'owner' | 'user';

/** One entry of a bot's access list: a person (`user_id`) or a team (`team_id`). */
export type BotGrant = {
  user_id: string | null; team_id: string | null; role: BotRole; name: string;
  /** A person's primary team, for display. */
  team?: string | null; is_active?: boolean;
};
export type BotGrantInput = { user_id?: string; team_id?: string; role: BotRole };

/** What a bot owner sees and maintains -- never the technical connection. */
export type OwnedBot = {
  id: string; kind: 'n8n' | 'llm'; name: string; description: string | null; enabled: boolean;
  system_prompt: string | null; retrieval_enabled: boolean; collections: string[];
  require_sources: boolean; no_context_reply: string; public: boolean; grants: BotGrant[]; updated_at: string;
};
export type OwnedBotUpdate = Partial<Pick<OwnedBot, 'description' | 'system_prompt' | 'collections' | 'require_sources' | 'no_context_reply' | 'public'>> & {
  /** Replaces the users; owners stay as the administrators set them. */
  grants?: BotGrantInput[];
};

export const toGrantInputs = (grants: BotGrant[]): BotGrantInput[] => grants.map(grant => grant.user_id
  ? { user_id: grant.user_id, role: grant.role }
  : { team_id: grant.team_id as string, role: grant.role });

export function listOwnedBots(signal?: AbortSignal): Promise<{ items: OwnedBot[] }> {
  return apiJson('/api/v1/bots', { signal });
}

export function updateOwnedBot(botId: string, update: OwnedBotUpdate): Promise<OwnedBot> {
  return apiJson(`/api/v1/bots/${encodeURIComponent(botId)}`, { ...jsonBody(update), method: 'PATCH' });
}
