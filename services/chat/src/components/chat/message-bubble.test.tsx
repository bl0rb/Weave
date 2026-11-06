// @vitest-environment jsdom
//
// Covers the agent-mode "status chips" rendered on a still-streaming
// assistant message (see message-bubble.tsx's own `AgentStatusChips`) —
// asserts the German chip label for each `UiAgentStatus.state`.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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

describe('MessageBubble answer notices', () => {
  afterEach(() => cleanup());

  const source = (documentId: string, notice: string | null) => ({
    source: 'confluence',
    document_id: documentId,
    chunk_id: 1,
    collection: 'intern',
    notice,
  });

  it('flags the answer once per distinct notice, even when sources are hidden', () => {
    const message: UiMessage = {
      ...makeMessage([]),
      streaming: false,
      sources: [source('a', 'Nur intern'), source('b', 'Nur intern'), source('c', null)] as UiMessage['sources'],
    };
    render(<MessageBubble message={message} showSources={false} />);
    expect(screen.getByTestId('answer-notices').textContent).toBe('Nur intern');
  });

  it('shows no flag without a notice', () => {
    const message: UiMessage = { ...makeMessage([]), streaming: false, sources: [source('a', null)] as UiMessage['sources'] };
    render(<MessageBubble message={message} />);
    expect(screen.queryByTestId('answer-notices')).toBeNull();
  });
});

describe('MessageBubble knowledge spaces and citations', () => {
  afterEach(() => cleanup());

  const cited = (documentId: string, collection: string, ref: number, notice: string | null = null) => ({
    source: 'confluence',
    original_filename: `${documentId}.pdf`,
    document_id: documentId,
    chunk_id: 1,
    collection,
    notice,
    ref,
  });

  const answer = (content: string, sources: unknown[]): UiMessage => ({
    ...makeMessage([]),
    streaming: false,
    content,
    sources: sources as UiMessage['sources'],
  });

  it('puts the notice below the answer text', () => {
    const { container } = render(<MessageBubble message={answer('Antwort [1].', [cited('a', 'intern', 1, 'Nur intern')])} />);
    const text = container.querySelector('.prose-chat')!;
    const notice = screen.getByTestId('answer-notices');
    expect(text.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('colours the passages of an answer mixing knowledge spaces and names the spaces', () => {
    const { container } = render(
      <MessageBubble message={answer('Erstens gilt A [1]. Zweitens gilt B [2][3].', [cited('a', 'hr', 1), cited('b', 'it', 2), cited('c', 'hr', 3)])} />
    );
    const passages = [...container.querySelectorAll('.cite-passage')];
    expect(passages.map((passage) => passage.textContent)).toEqual(['Erstens gilt A ', '. Zweitens gilt B ']);
    expect(passages.map((passage) => passage.getAttribute('data-mixed'))).toEqual(['true', 'true']);
    expect((passages[0] as HTMLElement).style.getPropertyValue('--space')).toBe('var(--space-1)');
    expect((passages[1] as HTMLElement).style.getPropertyValue('--space')).toBe('var(--space-2)');
    expect([...container.querySelectorAll('.cite-marker')].map((marker) => marker.textContent)).toEqual(['1', '2', '3']);
    expect(screen.getByTestId('answer-spaces').textContent).toContain('hr');
    expect(screen.getByTestId('answer-spaces').textContent).toContain('it');
  });

  it('does not colour an answer from a single knowledge space', () => {
    const { container } = render(<MessageBubble message={answer('Nur A [1].', [cited('a', 'hr', 1)])} />);
    expect(container.querySelector('.cite-passage')?.getAttribute('data-mixed')).toBe('false');
    expect(screen.queryByTestId('answer-spaces')).toBeNull();
  });

  it('leaves markers without a cited source as plain text', () => {
    const { container } = render(<MessageBubble message={answer('Fakt [7].', [{ ...cited('a', 'hr', 1), ref: null }])} />);
    expect(container.querySelector('.cite-marker')).toBeNull();
    expect(container.querySelector('.prose-chat')?.textContent).toBe('Fakt [7].');
  });

  it('marks the passages a hovered source backs', () => {
    const { container } = render(
      <MessageBubble message={answer('A [1]. B [2].', [cited('a', 'hr', 1), cited('b', 'it', 2)])} />
    );
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    const card = container.querySelectorAll('.source-card')[1];
    fireEvent.mouseEnter(card);
    const passages = [...container.querySelectorAll('.cite-passage')];
    expect(passages.map((passage) => passage.getAttribute('data-active'))).toEqual(['false', 'true']);
    expect(card.getAttribute('data-active')).toBe('true');
    fireEvent.mouseLeave(card);
    expect(container.querySelector('.cite-passage[data-active="true"]')).toBeNull();
  });
});

describe('MessageBubble answer formatting', () => {
  afterEach(() => cleanup());

  const html = (content: string) => {
    const { container } = render(<MessageBubble message={{ ...makeMessage([]), streaming: false, content }} />);
    return container.querySelector('.prose-chat')!.innerHTML;
  };

  it('keeps the line breaks the model wrote', () => {
    expect(html('Adresse:\nMusterstraße 1\n12345 Musterstadt')).toBe('<p>Adresse:<br>\nMusterstraße 1<br>\n12345 Musterstadt</p>');
  });

  it('renders "•" bullets as a list', () => {
    expect(html('Folgendes gilt:\n• Punkt eins\n• Punkt zwei')).toContain('<ul>\n<li>Punkt eins</li>\n<li>Punkt zwei</li>\n</ul>');
  });

  it('turns a <br> in a table cell into a line break instead of dropping it', () => {
    expect(html('| A | B |\n|---|---|\n| x<br>y | 2 |')).toContain('<td>x<br>\ny</td>');
  });

  it('leaves code blocks alone', () => {
    expect(html('```\n• kein Punkt\nzweite Zeile\n```')).toContain('<code>• kein Punkt\nzweite Zeile\n</code>');
  });
});
