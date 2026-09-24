'use client';

import { useEffect, useRef } from 'react';
import { MessageBubble } from '@/components/chat/message-bubble';
import type { UiMessage } from '@/lib/chat-types';

interface MessageListProps {
  messages: UiMessage[];
  assistantName?: string;
  onResetScopeAndRetry?: () => void;
  selectedSourceMessageId: string | null;
  onSelectForSourcesPanel: (id: string) => void;
}

export function MessageList({
  messages,
  assistantName,
  onResetScopeAndRetry,
  selectedSourceMessageId,
  onSelectForSourcesPanel,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-[var(--foreground-muted)]">
        Noch keine Nachrichten — stelle unten deine erste Frage.
      </div>
    );
  }

  return (
    // `role="log"` is the ARIA role built for exactly this: a
    // sequentially-appended transcript, implicitly `aria-live="polite"` —
    // announces a finished turn without re-reading the whole history on
    // every render (design target 5, "aria-live for new answers").
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4" role="log" aria-label="Gespräch">
      {messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          assistantName={assistantName}
          onResetScopeAndRetry={onResetScopeAndRetry}
          isSelectedForSourcesPanel={message.id === selectedSourceMessageId}
          onSelectForSourcesPanel={() => onSelectForSourcesPanel(message.id)}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
