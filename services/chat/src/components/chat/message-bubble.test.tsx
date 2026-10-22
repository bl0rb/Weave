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

describe('MessageBubble clarification chips', () => {
  afterEach(cleanup);

  function clarifyMessage(): UiMessage {
    const base = makeMessage([]);
    return {
      ...base,
      streaming: false,
      content: 'Meinst du eines dieser Dokumente?',
      trace: {
        intent: 'knowledge',
        confidence: 0.9,
        needs_retrieval: true,
        needs_tool: false,
        retrieval: null,
        guard: { triggered: true, reason: 'clarify' },
        model: null,
        timings_ms: {},
        n8n: null,
        agent: null,
        clarification: {
          question: 'Meinst du eines dieser Dokumente?',
          options: [
            { label: 'Arbeitsvertrag', message: 'Frage? (Dokument: Arbeitsvertrag)', document_id: 'doc-1', collection: null },
            { label: 'Betriebsvereinbarung', message: 'Frage? (Dokument: Betriebsvereinbarung)', document_id: 'doc-2', collection: null },
          ],
        },
      } as unknown as UiMessage['trace'],
    };
  }

  it('renders one chip per option and sends the ready-made follow-up on click', () => {
    const sent: string[] = [];
    render(<MessageBubble message={clarifyMessage()} onClarifyOption={(text) => sent.push(text)} />);
    const group = screen.getByTestId('clarification-options');
    expect(group.querySelectorAll('button')).toHaveLength(2);
    (screen.getByRole('button', { name: 'Betriebsvereinbarung' }) as HTMLButtonElement).click();
    expect(sent).toEqual(['Frage? (Dokument: Betriebsvereinbarung)']);
  });

  it('renders no chips without a handler', () => {
    render(<MessageBubble message={clarifyMessage()} />);
    expect(screen.queryByTestId('clarification-options')).toBeNull();
  });

  it('renders intake answer options without document ids and no guard banner', () => {
    const base = clarifyMessage();
    const trace = base.trace as unknown as Record<string, unknown>;
    const message = {
      ...base,
      content: 'Um welche Beschäftigungsart geht es?',
      trace: {
        ...trace,
        guard: { triggered: true, reason: 'intake', topic: null },
        clarification: {
          question: 'Um welche Beschäftigungsart geht es?',
          options: [
            { label: 'Vollzeit', message: 'Vollzeit', document_id: null, collection: null },
            { label: 'Teilzeit', message: 'Teilzeit', document_id: null, collection: null },
          ],
        },
      } as unknown as UiMessage['trace'],
    };
    const sent: string[] = [];
    render(<MessageBubble message={message} onClarifyOption={(text) => sent.push(text)} />);
    expect(screen.getByRole('group', { name: 'Antwortmöglichkeiten' }).querySelectorAll('button')).toHaveLength(2);
    (screen.getByRole('button', { name: 'Teilzeit' }) as HTMLButtonElement).click();
    expect(sent).toEqual(['Teilzeit']);
    expect(screen.queryByText('Keine Belege gefunden.')).toBeNull();
  });
});
