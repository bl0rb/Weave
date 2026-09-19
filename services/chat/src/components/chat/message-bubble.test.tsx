// @vitest-environment jsdom
//
// Covers the agent-mode "status chips" rendered on a still-streaming
// assistant message (see message-bubble.tsx's own `AgentStatusChips`) —
// asserts the German chip label for each `UiAgentStatus.state`.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MessageBubble } from '@/components/chat/message-bubble';
import type { UiAgentStatus, UiMessage } from '@/lib/chat-types';

function makeMessage(agentStatuses: UiAgentStatus[]): UiMessage {
  return {
    id: '1',
    role: 'assistant',
    content: 'Teilantwort …',
    streaming: true,
    sources: null,
    trace: null,
    error: null,
    viaFallback: false,
    slowResponse: false,
    progressLine: null,
    agentStatuses,
  };
}

describe('MessageBubble agent status chips', () => {
  afterEach(() => cleanup());

  it('renders a "fertig" chip for a complete subagent', () => {
    render(<MessageBubble message={makeMessage([{ agentId: 'it-support', agentName: 'IT Support', state: 'complete' }])} />);
    expect(screen.getByText('IT Support – fertig')).toBeTruthy();
  });

  it('renders a "teilweise" chip for a partial subagent', () => {
    render(<MessageBubble message={makeMessage([{ agentId: 'hr', agentName: 'HR', state: 'partial' }])} />);
    expect(screen.getByText('HR – teilweise')).toBeTruthy();
  });

  it('renders a "fehlgeschlagen" chip for a failed subagent', () => {
    render(<MessageBubble message={makeMessage([{ agentId: 'legal', agentName: 'Legal', state: 'failed' }])} />);
    expect(screen.getByText('Legal – fehlgeschlagen')).toBeTruthy();
  });

  it('renders all three states together, each with its own label', () => {
    render(
      <MessageBubble
        message={makeMessage([
          { agentId: 'it-support', agentName: 'IT Support', state: 'complete' },
          { agentId: 'hr', agentName: 'HR', state: 'partial' },
          { agentId: 'legal', agentName: 'Legal', state: 'failed' },
        ])}
      />
    );
    expect(screen.getByText('IT Support – fertig')).toBeTruthy();
    expect(screen.getByText('HR – teilweise')).toBeTruthy();
    expect(screen.getByText('Legal – fehlgeschlagen')).toBeTruthy();
  });
});
