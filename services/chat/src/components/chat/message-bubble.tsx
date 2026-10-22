import { useState, type ImgHTMLAttributes } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import { PanelRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { UiAgentStatus, UiMessage } from '@/lib/chat-types';
import { SourceCards } from '@/components/chat/source-cards';
import { WeaveLogo } from '@/components/weave-logo';
import { TracePanel } from '@/components/chat/trace-panel';
import { GuardBanner } from '@/components/chat/guard-banner';
import { ErrorBanner } from '@/components/chat/error-banner';
import { toProxiedImageUrl } from '@/lib/portal-artifact-url';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';

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

interface MessageBubbleProps {
  message: UiMessage;
  /** The assistant currently selected for this conversation — used for the
   * answer header's own name (see this component's `AnswerHead` below).
   * Not per-message: one conversation always belongs to one bot. Optional,
   * defaulting to a generic label, so call sites that only care about
   * other message content (this component's own existing tests) don't
   * need to thread a bot name through just to render at all. */
  assistantName?: string;
  /** Clears the composer's knowledge-space selection and resends the
   * question this answer responded to — forwarded to `GuardBanner`, which
   * only renders it as a button for `reason: 'filter_excluded_all'`. */
  onResetScopeAndRetry?: () => void;
  /** Whether this message is the one currently shown in the (open)
   * right-hand sources panel (see chat-app.tsx's `selectedSourceMessageId`)
   * — drives the pressed state of this bubble's own "Quellen anzeigen"
   * control below the answer. */
  isSelectedForSourcesPanel?: boolean;
  onSelectForSourcesPanel?: () => void;
  /** `false` (the bot's `show_sources`) hides this answer's source cards
   * and its "Quellen anzeigen" control. */
  showSources?: boolean;
  /** Called with a clarification option's ready-made follow-up text when
   * the person picks one of the document chips a `guard.reason: 'clarify'`
   * answer offers (`trace.clarification`, see types/weave-api.ts). Without
   * it the chips are not rendered at all. */
  onClarifyOption?: (message: string) => void;
}

export function MessageBubble({
  message,
  assistantName,
  onResetScopeAndRetry,
  isSelectedForSourcesPanel,
  onSelectForSourcesPanel,
  onClarifyOption,
  showSources = true,
}: MessageBubbleProps) {
  const { t } = useI18n();
  const clarification =
    message.role !== 'user' && !message.streaming ? (message.trace?.clarification ?? null) : null;
  const isUser = message.role === 'user';
  const sourceCount = message.sources?.length ?? 0;

  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'rounded-[var(--radius-card)] px-4 py-3 text-sm leading-relaxed',
          isUser
            ? 'max-w-[80ch] bg-[var(--accent)] text-[var(--on-accent)]'
            // An answer sizes itself by its content: running text stays at
            // a readable 80ch (see globals.css's `.chat-answer` rules), while
            // a wide table, code block or image may use the whole column.
            : 'chat-answer min-w-0 max-w-full border border-[var(--line)] bg-[var(--surface)] text-[var(--ink)]'
        )}
      >
        {!isUser ? (
          <AnswerHead
            assistantName={assistantName ?? t('chat.messageBubble.defaultAssistantName')}
            working={message.streaming}
            sourceCount={sourceCount}
            guardTriggered={message.trace?.guard?.triggered ?? false}
          />
        ) : null}

        {!isUser && message.trace?.guard ? (
          <GuardBanner guard={message.trace.guard} onResetScopeAndRetry={onResetScopeAndRetry} />
        ) : null}

        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : message.content ? (
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]} components={{ img: AnswerImage }}>
              {message.content}
            </ReactMarkdown>
          </div>
        ) : message.streaming ? (
          <span className="inline-flex items-center gap-1 text-[var(--muted)]" aria-live="polite">
            {/* Default placeholder stays screen-reader-only — only the
                progress-line/slow-response variant below is ever shown
                visibly. `progressLine` (an agent-mode turn's own live
                status message, e.g. "IT Support wird durchsucht") takes
                priority over the generic placeholder while it is set, and
                is cleared by chat-app.tsx the moment real answer text
                starts arriving (`onDelta`). */}
            <span className="sr-only">
              {message.progressLine ?? (message.slowResponse ? t('chat.messageBubble.stillWorking') : t('chat.messageBubble.generating'))}
            </span>
            {message.progressLine || message.slowResponse ? (
              <span className="text-xs" aria-hidden="true">
                {message.progressLine ?? t('chat.messageBubble.stillWorking')}
              </span>
            ) : null}
            <ThinkingDots />
          </span>
        ) : null}

        {!isUser && message.streaming && message.agentStatuses.length > 0 ? (
          <AgentStatusChips statuses={message.agentStatuses} />
        ) : null}

        {!isUser && message.streaming && message.content ? (
          <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-[var(--muted)] align-text-bottom motion-reduce:animate-none" aria-hidden="true" />
        ) : null}

        {/* The 30s-idle "still working" indicator must also fire mid-stream,
            once the answer already has partial content — the empty-content
            branch above only covers require_sources-buffered bots whose
            content stays "" until the final delta. An incrementally
            forwarding bot (the actual long-running n8n-agent case) has
            non-empty content from the first delta onward, so it needs its
            own visible cue here instead of relying on that branch. */}
        {!isUser && message.streaming && message.content && message.slowResponse ? (
          <p className="mt-1 text-xs text-[var(--muted)]" aria-live="polite">
            {t('chat.messageBubble.stillWorking')}
          </p>
        ) : null}

        {clarification && clarification.options.length > 0 && onClarifyOption ? (
          <div
            className="mt-2 flex flex-wrap gap-2"
            role="group"
            aria-label={t(message.trace?.guard?.reason === 'intake' ? 'chat.clarification.intakeOptionsLabel' : 'chat.clarification.optionsLabel')}
            data-testid="clarification-options"
          >
            {clarification.options.map((option) => (
              <button
                key={option.document_id ?? option.label}
                type="button"
                className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-1 text-xs hover:bg-[var(--surface-hover)]"
                onClick={() => onClarifyOption(option.message)}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : null}
        {/* Only on the ≥1180px layout that has a sources panel at all (see
            globals.css's `.chat-sources-panel-control`); below it the
            inline source cards right after carry their own toggle. */}
        {!isUser && showSources && sourceCount > 0 && !message.streaming && onSelectForSourcesPanel ? (
          <button
            type="button"
            onClick={onSelectForSourcesPanel}
            aria-pressed={!!isSelectedForSourcesPanel}
            className={cn(
              'chat-sources-panel-control mt-3 min-h-[40px] items-center gap-1 rounded-[var(--radius-control)] border border-[var(--line)] px-2.5 py-1 text-xs font-medium sm:min-h-0',
              isSelectedForSourcesPanel
                ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                : 'text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]'
            )}
          >
            <PanelRight className="h-3.5 w-3.5" aria-hidden="true" />
            {t('chat.messageBubble.showSources')}
          </button>
        ) : null}
        {!isUser && showSources && message.sources ? (
          <div className="chat-inline-sources">
            <SourceCards sources={message.sources} />
          </div>
        ) : null}
        {!isUser && message.trace ? <TracePanel trace={message.trace} /> : null}
        {!isUser && message.viaFallback ? (
          <p className="mt-2 text-[11px] italic text-[var(--muted)]">{t('chat.messageBubble.viaFallback')}</p>
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

/**
 * The assistant name + evidence badge above every answer (design target
 * "answer rendering" item 4): "Belegt durch N Quellen" once this turn's
 * sources are known, "Keine passende Quelle" once its guard has triggered
 * instead, or neither while both are still unknown (still streaming, or a
 * non-retrieval bot's plain reply). The "Quellen anzeigen" control that
 * opens the sources panel sits below the answer instead (see above).
 */
function AnswerHead({
  assistantName,
  working,
  sourceCount,
  guardTriggered,
}: {
  assistantName: string;
  /** Still streaming: the mark keeps weaving, like a "thinking" indicator. */
  working?: boolean;
  sourceCount: number;
  guardTriggered: boolean;
}) {
  const { t } = useI18n();
  // An answer written in this session keeps weaving its five rounds after it
  // finishes instead of stopping mid-motion; a turn loaded from history
  // (mounted with working=false) stays static.
  const [live] = useState(working);
  return (
    <div className="chat-answer-head mb-2 flex flex-wrap items-center gap-2">
      <WeaveLogo className="h-6 w-6 flex-none" animation={working ? 'loop' : live ? 'finite' : undefined} />
      <span className="text-[13px] font-semibold">{assistantName}</span>
      {sourceCount > 0 ? (
        <span className="rounded-full bg-[var(--ok-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--ok)]">
          {t('chat.messageBubble.sourcedBy', { count: sourceCount })}
        </span>
      ) : guardTriggered ? (
        <span className="rounded-full bg-[var(--warn-bg)] px-2 py-0.5 text-[11px] font-semibold text-[var(--warn)]">
          {t('chat.messageBubble.noMatchingSource')}
        </span>
      ) : null}
    </div>
  );
}

/** Small per-subagent chips for a still-streaming agent-mode turn — a live,
 * at-a-glance preview of what `TracePanel` will show read-only once the
 * turn's own `trace` event arrives (rendered only while `message.streaming`
 * — see message-bubble.tsx's own call site). Purely a rendering of already
 * public `UiAgentStatus` entries, never a prompt/query. */
function AgentStatusChips({ statuses }: { statuses: UiAgentStatus[] }) {
  const { t } = useI18n();
  const label: Record<string, MessageKey> = {
    complete: 'chat.messageBubble.status.complete',
    partial: 'chat.messageBubble.status.partial',
    failed: 'chat.messageBubble.status.failed',
  };
  const tone: Record<string, string> = {
    complete: 'border-[var(--ok,#2f9e44)] text-[var(--ok,#2f9e44)]',
    partial: 'border-[var(--warn,#e8a33d)] text-[var(--warn,#e8a33d)]',
    failed: 'border-[var(--err,#e03131)] text-[var(--err,#e03131)]',
  };
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {statuses.map((status) => (
        <span
          key={status.agentId}
          className={cn(
            'rounded-full border px-2 py-0.5 text-[11px]',
            status.state ? tone[status.state] : 'border-[var(--line)] text-[var(--muted)]'
          )}
        >
          {status.agentName}
          {status.state ? ` – ${t(label[status.state])}` : ' …'}
        </span>
      ))}
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="flex gap-1" aria-hidden="true">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s] motion-reduce:animate-none" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s] motion-reduce:animate-none" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current motion-reduce:animate-none" />
    </span>
  );
}
