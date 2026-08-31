'use client';

import { useEffect, useRef } from 'react';
import { MessageBubble } from '@/components/chat/message-bubble';
import type { UiMessage } from '@/lib/chat-types';

export function MessageList({ messages }: { messages: UiMessage[] }) {
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
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
