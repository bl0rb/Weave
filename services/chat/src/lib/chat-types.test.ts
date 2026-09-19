import { describe, expect, it } from 'vitest';
import { applyAgentStatus, buildChatRequestBody, pendingAssistantMessage } from '@/lib/chat-types';
import type { ChatStreamStatusEvent } from '@/types/weave-api';

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

describe('pendingAssistantMessage', () => {
  it('starts with no progress line and no agent statuses', () => {
    const message = pendingAssistantMessage();
    expect(message.progressLine).toBeNull();
    expect(message.agentStatuses).toEqual([]);
  });
});

describe('applyAgentStatus', () => {
  const started: ChatStreamStatusEvent = {
    type: 'status', stage: 'researching', agent_id: 'it-support', agent_name: 'IT Support',
    state: null, message: 'IT Support wird durchsucht',
  };
  const finished: ChatStreamStatusEvent = { ...started, state: 'complete' };

  it('ignores a non-researching status event (planning/merging/answering have no agent chip)', () => {
    const planning: ChatStreamStatusEvent = {
      type: 'status', stage: 'planning', agent_id: null, agent_name: null, state: null, message: 'Anfrage wird geplant',
    };
    expect(applyAgentStatus([], planning)).toEqual([]);
  });

  it('appends a new chip for a subagent seen for the first time this turn', () => {
    const result = applyAgentStatus([], started);
    expect(result).toEqual([{ agentId: 'it-support', agentName: 'IT Support', state: null }]);
  });

  it('updates that same agent in place once its finish event arrives, never duplicating the chip', () => {
    const afterStart = applyAgentStatus([], started);
    const afterFinish = applyAgentStatus(afterStart, finished);
    expect(afterFinish).toEqual([{ agentId: 'it-support', agentName: 'IT Support', state: 'complete' }]);
  });

  it('keeps a second agent as its own entry alongside the first', () => {
    const hrStarted: ChatStreamStatusEvent = {
      ...started, agent_id: 'hr-support', agent_name: 'HR Support',
    };
    const result = applyAgentStatus(applyAgentStatus([], started), hrStarted);
    expect(result.map((s) => s.agentId)).toEqual(['it-support', 'hr-support']);
  });
});
