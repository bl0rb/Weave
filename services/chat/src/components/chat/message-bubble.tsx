import type { ImgHTMLAttributes } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import { cn } from '@/lib/utils';
import type { UiMessage } from '@/lib/chat-types';
import { SourceCards } from '@/components/chat/source-cards';
import { TracePanel } from '@/components/chat/trace-panel';
import { GuardBanner } from '@/components/chat/guard-banner';
import { ErrorBanner } from '@/components/chat/error-banner';
import { toProxiedImageUrl } from '@/lib/portal-artifact-url';

// Rewrites an inline `![alt](url)` image's src to this app's own proxy
// route (see lib/portal-artifact-url.ts) before it ever reaches the DOM —
// the model is instructed to keep source image markdown verbatim
// (Weave-Runtime's `_context_block`), so an answer can contain a
// Weave-Ingest release-artifact URL the browser must never fetch directly.
function AnswerImage({ src, alt }: ImgHTMLAttributes<HTMLImageElement>) {
  if (!src || typeof src !== 'string') return null;
  // eslint-disable-next-line @next/next/no-img-element -- proxied, per-message remote image, not a static asset
  return <img src={toProxiedImageUrl(src)} alt={alt ?? ''} className="max-w-full rounded-lg" />;
}

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
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]} components={{ img: AnswerImage }}>
              {message.content}
            </ReactMarkdown>
          </div>
        ) : message.streaming ? (
          <span className="inline-flex items-center gap-1 text-[var(--foreground-muted)]" aria-live="polite">
            {/* Default placeholder stays screen-reader-only (see sidebar.tsx's
                comment referencing this same pattern) — only the slow-response
                variant below is ever shown visibly. */}
            <span className="sr-only">
              {message.slowResponse ? 'Der Assistent arbeitet noch …' : 'Antwort wird erzeugt…'}
            </span>
            {message.slowResponse ? (
              <span className="text-xs" aria-hidden="true">
                Der Assistent arbeitet noch …
              </span>
            ) : null}
            <ThinkingDots />
          </span>
        ) : null}

        {!isUser && message.streaming && message.content ? (
          <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-[var(--foreground-muted)] align-text-bottom" aria-hidden="true" />
        ) : null}

        {/* The 30s-idle "still working" indicator must also fire mid-stream,
            once the answer already has partial content — the empty-content
            branch above only covers require_sources-buffered bots whose
            content stays "" until the final delta. An incrementally
            forwarding bot (the actual long-running n8n-agent case) has
            non-empty content from the first delta onward, so it needs its
            own visible cue here instead of relying on that branch. */}
        {!isUser && message.streaming && message.content && message.slowResponse ? (
          <p className="mt-1 text-xs text-[var(--foreground-muted)]" aria-live="polite">
            Der Assistent arbeitet noch …
          </p>
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
