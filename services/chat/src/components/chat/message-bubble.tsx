import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import { cn } from '@/lib/utils';
import type { UiMessage } from '@/lib/chat-types';
import { SourceCards } from '@/components/chat/source-cards';
import { TracePanel } from '@/components/chat/trace-panel';
import { GuardBanner } from '@/components/chat/guard-banner';
import { ErrorBanner } from '@/components/chat/error-banner';

export function MessageBubble({ message }: { message: UiMessage }) {
  const isUser = message.role === 'user';

  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[80ch] rounded-2xl px-4 py-3 text-sm leading-relaxed',
          isUser
            ? 'bg-[var(--accent)] text-[var(--accent-foreground)]'
            : 'border border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)]'
        )}
      >
        {!isUser && message.trace?.guard ? <GuardBanner guard={message.trace.guard} /> : null}

        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : message.content ? (
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
              {message.content}
            </ReactMarkdown>
          </div>
        ) : message.streaming ? (
          <span className="inline-flex items-center gap-1 text-[var(--foreground-muted)]" aria-live="polite">
            <span className="sr-only">Antwort wird erzeugt…</span>
            <ThinkingDots />
          </span>
        ) : null}

        {!isUser && message.streaming && message.content ? (
          <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-[var(--foreground-muted)] align-text-bottom" aria-hidden="true" />
        ) : null}

        {!isUser && message.sources ? <SourceCards sources={message.sources} /> : null}
        {!isUser && message.trace ? <TracePanel trace={message.trace} /> : null}
        {!isUser && message.viaFallback ? (
          <p className="mt-2 text-[11px] italic text-[var(--foreground-muted)]">
            Nicht gestreamt — als Fallback über die nicht-streamende Schnittstelle beantwortet.
          </p>
        ) : null}
        {!isUser && message.error ? (
          <div className="mt-2">
            <ErrorBanner error={message.error} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="flex gap-1" aria-hidden="true">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current" />
    </span>
  );
}
