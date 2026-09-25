'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Lock, Menu } from 'lucide-react';
import { Rail } from '@/components/chat/rail';
import { MessageList } from '@/components/chat/message-list';
import { Composer } from '@/components/chat/composer';
import { SourcesPanel } from '@/components/chat/sources-panel';
import { ErrorBanner } from '@/components/chat/error-banner';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';
import { deleteJson, getJson, postJson } from '@/lib/api-client';
import { errorForInterruptedStream, errorForNetworkFailure, errorForStreamEvent, mappedError, type MappedError } from '@/lib/errors';
import { consumeChatStream } from '@/lib/run-chat-stream';
import {
  applyAgentStatus,
  buildChatRequestBody,
  newId,
  pendingAssistantMessage,
  scopeLabel,
  uiMessageFromStored,
  userMessage,
  type UiMessage,
} from '@/lib/chat-types';
import type { Bot, ChatRequestBody, ChatResponseBody, Collection, ConversationSummary, MeResponse, StoredConversation } from '@/types/weave-api';

export function ChatApp() {
  const router = useRouter();
  const { t, locale, applyLocale } = useI18n();

  const [bots, setBots] = useState<Bot[] | null>(null);
  const [botsError, setBotsError] = useState<MappedError | null>(null);
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [collectionsError, setCollectionsError] = useState<MappedError | null>(null);
  // The composer's knowledge-space (scope-picker) selection — a real
  // per-request FILTER (see ChatRequestBody.collections's own docstring in
  // types/weave-api.ts) — empty means "no filter", not "filter to
  // nothing". Deliberately NOT reset on bot switch / new conversation: it
  // represents what the user wants to search, independent of which bot or
  // conversation that happens in — a slug the next bot's own scope has no
  // use for is silently dropped server-side, exactly like any other
  // out-of-scope filter entry (contracts/internal-chat.md).
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);

  const [selectedBotId, setSelectedBotId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);

  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [conversationsError, setConversationsError] = useState<MappedError | null>(null);

  // The last question actually sent, kept for the guard banner's own
  // "Auswahl zurücksetzen & neu fragen" action (filter_excluded_all) — see
  // handleResetScopeAndRetry below. Distinct from `draft`, which by then
  // has already been cleared back to "".
  const [lastSentMessage, setLastSentMessage] = useState<string | null>(null);

  // Which assistant message's sources the right-hand SourcesPanel shows —
  // `null` means "the latest one" (see `activeSourceMessage` below), the
  // default per design target 2. Reset to `null` on every new turn/
  // conversation switch so the panel keeps following the newest answer
  // unless the caller explicitly pins an older one.
  const [selectedSourceMessageId, setSelectedSourceMessageId] = useState<string | null>(null);

  // The rail's own off-canvas drawer state below the 900px breakpoint (see
  // globals.css's `.chat-rail`) — irrelevant, and always effectively
  // "open", above it. `railToggleRef` gets focus back once the drawer
  // closes, so keyboard/screen-reader users land back on the control that
  // opened it instead of losing their place.
  const [railOpen, setRailOpen] = useState(false);
  const railToggleRef = useRef<HTMLButtonElement>(null);

  function closeRail() {
    setRailOpen(false);
    railToggleRef.current?.focus();
  }

  // Guards against setting state from a turn the user has since abandoned
  // (switched bot / started a new one) while its request was in flight.
  const activeTurnRef = useRef<string | null>(null);

  // Re-fetches the rail's history list — called on mount and again after
  // anything that can change it (a turn creating/renaming a conversation,
  // or a delete) so the rail never goes stale.
  const refreshConversations = useCallback(async () => {
    const result = await getJson<ConversationSummary[]>('/api/conversations');
    if (result.ok) {
      setConversations(result.data);
    } else {
      setConversationsError(result.error);
      if (result.error.kind === 'auth_expired') router.replace('/login');
    }
  }, [router]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const [botsResult, collectionsResult, meResult] = await Promise.all([
        getJson<Bot[]>('/api/bots'),
        getJson<Collection[]>('/api/collections'),
        getJson<MeResponse>('/api/session/me'),
      ]);
      if (cancelled) return;

      if (botsResult.ok) {
        setBots(botsResult.data);
        setSelectedBotId((current) => current ?? botsResult.data[0]?.id ?? null);
      } else {
        setBotsError(botsResult.error);
        if (botsResult.error.kind === 'auth_expired') {
          router.replace('/login');
          return;
        }
      }

      if (collectionsResult.ok) {
        setCollections(collectionsResult.data);
      } else {
        setCollectionsError(collectionsResult.error);
      }

      // Account locale wins over whatever the cookie/browser picked, but
      // only once, right here at mount — `locale`/`applyLocale` are read
      // at mount time only (see the eslint-disable below); this must
      // never become a dependency of this effect, or every manual switch
      // via <LanguageSwitch/> would re-run it and immediately re-apply
      // the (by-then stale) account value.
      if (meResult.ok && meResult.data.locale && meResult.data.locale !== locale) {
        applyLocale(meResult.data.locale);
      }

      await refreshConversations();
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once on mount only
  }, []);

  function updateMessage(id: string, updater: (message: UiMessage) => UiMessage) {
    setMessages((prev) => prev.map((message) => (message.id === id ? updater(message) : message)));
  }

  // Both handlers below abandon whatever turn is currently in flight
  // (activeTurnRef.current = null makes every one of runTurn/runFallback's
  // `if (activeTurnRef.current !== turnId) return;` guards fire the next
  // time that turn's promise chain resumes, so it can no longer touch
  // `messages` for the conversation the user has since left). `sending`
  // must be reset in the SAME place: it is only ever cleared by
  // handleSend's own `finally` block, which is itself guarded by that same
  // `activeTurnRef.current === turnId` check — so once a turn is
  // abandoned here, that `finally` will see the check fail and skip
  // `setSending(false)` forever, leaving the Composer permanently
  // disabled. Deliberately NOT disabling these actions while `sending` is
  // true instead (the alternative fix): abandoning an in-flight turn is
  // the user's only way out of a slow or hung request short of reloading
  // the page, so both must stay clickable during one — that is exactly
  // the case this reset makes safe.
  function handleSelectBot(botId: string) {
    if (botId === selectedBotId) return;
    activeTurnRef.current = null;
    setSending(false);
    setSelectedBotId(botId);
    setConversationId(null);
    setMessages([]);
    setSelectedSourceMessageId(null);
  }

  function handleNewConversation() {
    activeTurnRef.current = null;
    setSending(false);
    setConversationId(null);
    setMessages([]);
    setSelectedSourceMessageId(null);
    closeRail();
  }

  function handleToggleCollection(slug: string) {
    setSelectedCollections((prev) => (prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug]));
  }

  function handleClearCollections() {
    setSelectedCollections([]);
  }

  const runFallback = useCallback(
    async (turnId: string, assistantId: string, body: ChatRequestBody) => {
      const result = await postJson<ChatResponseBody>('/api/chat', body);
      if (activeTurnRef.current !== turnId) return;

      if (!result.ok) {
        if (result.error.kind === 'auth_expired') {
          router.replace('/login');
          return;
        }
        updateMessage(assistantId, (m) => ({ ...m, streaming: false, error: result.error }));
        return;
      }

      setConversationId(result.data.conversation_id);
      updateMessage(assistantId, (m) => ({
        ...m,
        streaming: false,
        content: result.data.answer,
        sources: result.data.sources ?? [],
        trace: result.data.trace,
        viaFallback: true,
      }));
    },
    [router]
  );

  const runTurn = useCallback(
    async (turnId: string, assistantId: string, body: ChatRequestBody) => {
      let response: Response;
      try {
        response = await fetch('/api/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } catch {
        if (activeTurnRef.current !== turnId) return;
        // Could not even reach our own server — try the non-streaming
        // fallback once before giving up on this turn.
        await runFallback(turnId, assistantId, body);
        return;
      }

      if (activeTurnRef.current !== turnId) return;

      if (!response.ok) {
        const parsed = (await response.json().catch(() => null)) as MappedError | null;
        const error = parsed ?? mappedError('unknown', null);

        if (error.kind === 'auth_expired') {
          router.replace('/login');
          return;
        }
        if (error.kind === 'gateway_unreachable') {
          await runFallback(turnId, assistantId, body);
          return;
        }
        if (error.kind === 'conversation_not_found') {
          // The id this turn was continuing no longer resolves — drop it
          // so a retry starts a fresh conversation instead of repeating
          // the same failure.
          setConversationId(null);
        }
        updateMessage(assistantId, (m) => ({ ...m, streaming: false, error }));
        return;
      }

      const newConversationId = response.headers.get('x-conversation-id');
      if (newConversationId) setConversationId(newConversationId);

      if (!response.body) {
        updateMessage(assistantId, (m) => ({ ...m, streaming: false, error: mappedError('unknown', null) }));
        return;
      }

      // Long n8n agent runs can now legitimately go minutes between
      // deltas (kept alive underneath by the transport's own keepalive
      // comments — see sse.ts), which would otherwise leave the
      // "Antwort wird erzeugt…" placeholder looking stuck. Re-armed on
      // every delta; fires once after 30s of silence to swap the
      // placeholder copy. Cleared in the `finally` below so it never fires
      // after the turn has already ended, including when consumeChatStream
      // itself throws (e.g. a genuine transport error from reader.read()).
      let slowTimer: ReturnType<typeof setTimeout> | null = null;
      const armSlowTimer = () => {
        if (slowTimer) clearTimeout(slowTimer);
        slowTimer = setTimeout(() => {
          if (activeTurnRef.current === turnId) updateMessage(assistantId, (m) => ({ ...m, slowResponse: true }));
        }, 30_000);
      };
      armSlowTimer();

      let outcome: Awaited<ReturnType<typeof consumeChatStream>>;
      try {
        outcome = await consumeChatStream(response.body, {
          onTrace: (trace) => {
            if (activeTurnRef.current === turnId) updateMessage(assistantId, (m) => ({ ...m, trace }));
          },
          onDelta: (text) => {
            armSlowTimer();
            if (activeTurnRef.current === turnId) {
              updateMessage(assistantId, (m) => ({
                ...m, content: m.content + text, slowResponse: false, progressLine: null,
              }));
            }
          },
          onSources: (sources) => {
            if (activeTurnRef.current === turnId) updateMessage(assistantId, (m) => ({ ...m, sources }));
          },
          onStatus: (event) => {
            // Agent-mode research (planning/researching/merging) can take a
            // while with no `delta` yet -- re-arming here keeps the >30s
            // "arbeitet noch" hint keyed off genuine silence, not off an
            // agent turn that is actively reporting progress.
            armSlowTimer();
            if (activeTurnRef.current === turnId) {
              updateMessage(assistantId, (m) => ({
                ...m, progressLine: event.message, agentStatuses: applyAgentStatus(m.agentStatuses, event),
              }));
            }
          },
        });
      } finally {
        if (slowTimer) clearTimeout(slowTimer);
      }

      if (activeTurnRef.current !== turnId) return;

      if (outcome.status === 'done') {
        updateMessage(assistantId, (m) => ({ ...m, streaming: false }));
      } else if (outcome.status === 'error') {
        updateMessage(assistantId, (m) => ({ ...m, streaming: false, error: errorForStreamEvent(outcome.detail) }));
      } else {
        updateMessage(assistantId, (m) => ({ ...m, streaming: false, error: errorForInterruptedStream() }));
      }
    },
    [router, runFallback]
  );

  // Pulled out of handleSend so handleResetScopeAndRetry (the guard
  // banner's "Auswahl zurücksetzen & neu fragen" action) can resend the
  // last question against an EXPLICIT empty collections filter without
  // racing `setSelectedCollections`'s own async state update — React
  // batches that setState, so reading `selectedCollections` again in the
  // same tick would still see the OLD selection, not the just-cleared one.
  const sendTurn = useCallback(
    async (text: string, collectionsForRequest: string[]) => {
      const trimmed = text.trim();
      if (!trimmed || !selectedBotId || sending) return;

      const turnId = newId();
      activeTurnRef.current = turnId;

      const userMsg = userMessage(trimmed);
      const assistantMsg = pendingAssistantMessage();
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setLastSentMessage(trimmed);
      setSelectedSourceMessageId(null);
      setSending(true);

      const body = buildChatRequestBody({
        botId: selectedBotId,
        message: trimmed,
        conversationId,
        selectedCollections: collectionsForRequest,
      });

      try {
        await runTurn(turnId, assistantMsg.id, body);
      } catch (cause) {
        if (activeTurnRef.current === turnId) {
          updateMessage(assistantMsg.id, (m) => ({ ...m, streaming: false, error: errorForNetworkFailure(cause) }));
        }
      } finally {
        if (activeTurnRef.current === turnId) setSending(false);
        // The turn above may have created a brand-new conversation or
        // changed an existing one's title/updated_at — refresh regardless
        // of whether this turn was since abandoned, so the rail's history
        // never shows a stale list.
        void refreshConversations();
      }
    },
    [selectedBotId, conversationId, sending, runTurn, refreshConversations]
  );

  async function handleSend() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    setDraft('');
    await sendTurn(trimmed, selectedCollections);
  }

  // The guard banner's own one-click fix for `reason: 'filter_excluded_all'`
  // (see guard-banner.tsx): the caller's own knowledge-space selection is
  // what excluded every collection this bot could otherwise search, so
  // clearing it and resending the same question is guaranteed to be a
  // meaningfully different retry, not just a repeat of the same failure.
  function handleResetScopeAndRetry() {
    if (!lastSentMessage) return;
    setSelectedCollections([]);
    void sendTurn(lastSentMessage, []);
  }

  // Loads one past conversation's full transcript (GET
  // /api/conversations/{id}) back into view — same abandon-safety pattern
  // as handleSelectBot/handleNewConversation above: any turn still in
  // flight for whatever was open before is abandoned first.
  async function handleSelectConversation(id: string) {
    closeRail();
    if (id === conversationId) return;
    activeTurnRef.current = null;
    setSending(false);

    const result = await getJson<StoredConversation>(`/api/conversations/${id}`);
    if (!result.ok) {
      if (result.error.kind === 'auth_expired') {
        router.replace('/login');
        return;
      }
      setConversationsError(result.error);
      return;
    }

    setSelectedBotId(result.data.bot_id);
    setConversationId(result.data.id);
    setMessages(result.data.messages.map(uiMessageFromStored));
    setSelectedSourceMessageId(null);
  }

  // Permanently removes one past conversation (DELETE
  // /api/conversations/{id}). A destructive, irreversible action — same
  // window.confirm discipline as this codebase's other delete actions
  // (e.g. Weave-Ingest's import-sync.tsx / imports/[id]/page.tsx).
  async function handleDeleteConversation(id: string) {
    if (!window.confirm(t('chat.confirm.deleteConversation'))) return;

    const result = await deleteJson(`/api/conversations/${id}`);
    if (!result.ok) {
      if (result.error.kind === 'auth_expired') {
        router.replace('/login');
        return;
      }
      setConversationsError(result.error);
      return;
    }

    setConversations((prev) => prev?.filter((c) => c.id !== id) ?? prev);
    if (id === conversationId) {
      activeTurnRef.current = null;
      setSending(false);
      setConversationId(null);
      setMessages([]);
      setSelectedSourceMessageId(null);
    }
  }

  async function handleDeleteAllConversations() {
    if (!window.confirm(t('chat.confirm.deleteAllConversations'))) return;

    const result = await deleteJson('/api/conversations');
    if (!result.ok) {
      if (result.error.kind === 'auth_expired') {
        router.replace('/login');
        return;
      }
      setConversationsError(result.error);
      return;
    }

    activeTurnRef.current = null;
    setSending(false);
    setConversationId(null);
    setMessages([]);
    setConversations([]);
    setSelectedSourceMessageId(null);
  }

  const selectedBot = bots?.find((bot) => bot.id === selectedBotId) ?? null;
  const currentScopeLabel = scopeLabel(selectedCollections, collections, t);
  const conversationTitle = conversations?.find((c) => c.id === conversationId)?.title ?? t('chat.newConversation');

  // The right-hand sources panel always follows the latest assistant
  // answer that actually has sources unless the caller explicitly pinned
  // an older one (`selectedSourceMessageId`) — see message-list.tsx's own
  // "In Quellenleiste anzeigen" control and its reset points above.
  const assistantMessages = messages.filter((message) => message.role === 'assistant');
  const latestAnsweredMessage =
    [...assistantMessages].reverse().find((message) => (message.sources?.length ?? 0) > 0) ??
    assistantMessages[assistantMessages.length - 1] ??
    null;
  const activeSourceMessage =
    (selectedSourceMessageId ? assistantMessages.find((message) => message.id === selectedSourceMessageId) : null) ??
    latestAnsweredMessage;

  return (
    <div className="chat-shell">
      <Rail
        conversations={conversations}
        conversationsError={conversationsError}
        selectedConversationId={conversationId}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        onDeleteAllConversations={handleDeleteAllConversations}
        onNewConversation={handleNewConversation}
        newConversationDisabled={messages.length === 0}
        open={railOpen}
        onClose={closeRail}
      />

      <main className="flex min-w-0 min-h-0 flex-col">
        <header className="flex min-h-[62px] flex-none items-center gap-3 border-b border-[var(--line)] px-4 sm:px-6">
          <Button
            ref={railToggleRef}
            variant="outline"
            size="sm"
            onClick={() => setRailOpen(true)}
            aria-label={t('chat.header.openRail')}
            aria-expanded={railOpen}
            className="chat-mobile-menu hidden h-10 w-10 flex-none p-0"
          >
            <Menu className="h-4 w-4" aria-hidden="true" />
          </Button>

          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[17px] font-semibold">{conversationTitle}</h1>
            <p className="truncate text-[11px] text-[var(--muted)]">
              {selectedBot?.name ?? t('chat.header.noBotSelected')} · {currentScopeLabel}
            </p>
          </div>

          <span
            className="hidden flex-none items-center gap-1.5 rounded-[var(--radius-pill)] bg-[var(--accent-soft)] px-3 py-1.5 text-xs font-medium text-[var(--accent)] sm:inline-flex"
            title={t('chat.header.releasedSourcesOnlyTitle')}
          >
            <Lock className="h-3.5 w-3.5" aria-hidden="true" />
            {t('chat.header.releasedSourcesOnly')}
          </span>
        </header>

        {/* Bots failing to load is a hard blocker (the composer has
            nothing to send with) — surfaced here, prominently, same as the
            old sidebar did; a collections load failure is far less
            disruptive (retrieval still runs unfiltered) and stays scoped
            to the scope-picker popover that actually needs it instead. */}
        {botsError ? (
          <div className="flex-none px-4 pt-2 sm:px-6">
            <ErrorBanner error={botsError} />
          </div>
        ) : null}

        <MessageList
          messages={messages}
          assistantName={selectedBot?.name}
          onResetScopeAndRetry={handleResetScopeAndRetry}
          selectedSourceMessageId={activeSourceMessage?.id ?? null}
          onSelectForSourcesPanel={setSelectedSourceMessageId}
        />

        <Composer
          value={draft}
          onChange={setDraft}
          onSend={handleSend}
          disabled={sending}
          bots={bots}
          selectedBotId={selectedBotId}
          onSelectBot={handleSelectBot}
          collections={collections}
          collectionsError={collectionsError}
          selectedCollections={selectedCollections}
          onToggleCollection={handleToggleCollection}
          onClearCollections={handleClearCollections}
        />
      </main>

      <SourcesPanel sources={activeSourceMessage?.sources ?? null} scopeLabel={currentScopeLabel} />
    </div>
  );
}
