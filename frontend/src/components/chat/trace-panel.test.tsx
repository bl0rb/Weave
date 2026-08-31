// @vitest-environment jsdom
//
// Covers FINDING 3: `ChatTrace.n8n` must reach the UI, and a nonzero
// `dropped_sources` must render as a clearly-flagged warning, not an
// incidental number. See vitest.config.ts's own docstring for why this
// file opts into jsdom via a per-file pragma while the rest of the suite
// stays plain Node.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { TracePanel } from '@/components/chat/trace-panel';
import type { ChatTrace } from '@/types/weave-api';

function makeTrace(overrides: Partial<ChatTrace>): ChatTrace {
  return {
    intent: 'knowledge',
    confidence: 0.9,
    needs_retrieval: true,
    needs_tool: false,
    retrieval: null,
    model: 'gpt-4o',
    router_mode: 'rules',
    timings_ms: {},
    guard: null,
    n8n: null,
    ...overrides,
  };
}

function renderOpen(trace: ChatTrace) {
  render(<TracePanel trace={trace} />);
  fireEvent.click(screen.getByRole('button', { name: /Trace/ }));
}

describe('TracePanel', () => {
  afterEach(() => cleanup());

  it('renders nothing n8n-related for a turn that never reached n8n (n8n: null)', () => {
    renderOpen(makeTrace({}));
    expect(screen.queryByText(/n8n/)).toBeNull();
  });

  it('shows a plain line when no sources were dropped', () => {
    renderOpen(makeTrace({ n8n: { dropped_sources: 0 } }));
    expect(screen.getByText(/keine Quellen außerhalb des erlaubten Umfangs verworfen/)).toBeTruthy();
  });

  it('flags a nonzero dropped_sources count as a warning with a German explanation, not just a number', () => {
    renderOpen(makeTrace({ n8n: { dropped_sources: 3 } }));
    const warning = screen.getByText(/3 Quelle\(n\) verworfen\./);
    expect(warning).toBeTruthy();
    // Rendered in the warning-styled container, not a plain <Tag>.
    expect(warning.closest('[class*="warning"]')).not.toBeNull();
    expect(screen.getByText(/außerhalb seines erlaubten Collection-Umfangs/)).toBeTruthy();
  });
});
