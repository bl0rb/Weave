import { describe, expect, it } from 'vitest';
import { buildChatRequestBody } from '@/lib/chat-types';

describe('buildChatRequestBody', () => {
  it('omits both conversation_id and collections when there is no conversation yet and no filter selected', () => {
    const body = buildChatRequestBody({
      botId: 'legal-support',
      message: 'Hallo',
      conversationId: null,
      selectedCollections: [],
    });

    expect(body).toEqual({ bot_id: 'legal-support', message: 'Hallo' });
    expect(body).not.toHaveProperty('conversation_id');
    expect(body).not.toHaveProperty('collections');
  });

  it('sends the selected collections as the request filter when the sidebar selection is non-empty', () => {
    const body = buildChatRequestBody({
      botId: 'legal-support',
      message: 'Hallo',
      conversationId: null,
      selectedCollections: ['legal-2026', 'hr-docs'],
    });

    expect(body.collections).toEqual(['legal-2026', 'hr-docs']);
  });

  it('never sends collections as an explicit empty array — an empty selection means "no filter", not "matches nothing"', () => {
    const body = buildChatRequestBody({
      botId: 'legal-support',
      message: 'Hallo',
      conversationId: 'conv-1',
      selectedCollections: [],
    });

    expect(body).not.toHaveProperty('collections');
    expect(body.conversation_id).toBe('conv-1');
  });

  it('includes conversation_id and collections together once a conversation exists and a filter is selected', () => {
    const body = buildChatRequestBody({
      botId: 'legal-support',
      message: 'Weiter',
      conversationId: 'conv-1',
      selectedCollections: ['legal-2026'],
    });

    expect(body).toEqual({
      bot_id: 'legal-support',
      message: 'Weiter',
      conversation_id: 'conv-1',
      collections: ['legal-2026'],
    });
  });
});
