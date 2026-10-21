'use client';

import { useEffect, useRef } from 'react';
import { MessageBubble } from '@/components/chat/message-bubble';
import { useI18n } from '@/i18n/provider';
import type { UiMessage } from '@/lib/chat-types';

interface MessageListProps {
  messages: UiMessage[];
  assistantName?: string;
  /** Called with the id of the answer whose guard banner was clicked. */
  onResetScopeAndRetry?: (assistantMessageId: string) => void;
  /** Sends a clarification option's ready-made follow-up as the next user
   * message -- see `MessageBubble`'s own prop of the same name. */
  onClarifyOption?: (message: string) => void;
  selectedSourceMessageId: string | null;
  onSelectForSourcesPanel: (id: string) => void;
  /** The bot's `show_sources` — see `MessageBubble`'s prop of the same name. */
  showSources?: boolean;
}

export function MessageList({
  messages,
  assistantName,
  onResetScopeAndRetry,
  onClarifyOption,
  selectedSourceMessageId,
  onSelectForSourcesPanel,
  showSources,
}: MessageListProps) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-[var(--muted)]">
        {t('chat.messageList.empty')}
      </div>
    );
  }

  return (
    // `role="log"` is the ARIA role built for exactly this: a
    // sequentially-appended transcript, implicitly `aria-live="polite"` —
    // announces a finished turn without re-reading the whole history on
    // every render (design target 5, "aria-live for new answers").
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4" role="log" aria-label={t('chat.messageList.landmarkLabel')}>
      {messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          assistantName={assistantName}
          onResetScopeAndRetry={onResetScopeAndRetry ? () => onResetScopeAndRetry(message.id) : undefined}
          onClarifyOption={onClarifyOption}
          isSelectedForSourcesPanel={message.id === selectedSourceMessageId}
          onSelectForSourcesPanel={() => onSelectForSourcesPanel(message.id)}
          showSources={showSources}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
