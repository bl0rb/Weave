'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { RotateCcw } from 'lucide-react';
import { Sidebar } from '@/components/chat/sidebar';
import { HistoryPanel } from '@/components/chat/history-panel';
import { MessageList } from '@/components/chat/message-list';
import { Composer } from '@/components/chat/composer';
import { Button } from '@/components/ui/button';
import { deleteJson, getJson, postJson } from '@/lib/api-client';
import { errorForInterruptedStream, errorForNetworkFailure, errorForStreamEvent, mappedError, type MappedError } from '@/lib/errors';
import { consumeChatStream } from '@/lib/run-chat-stream';
import { buildChatRequestBody, newId, pendingAssistantMessage, uiMessageFromStored, userMessage, type UiMessage } from '@/lib/chat-types';
import type { Bot, ChatRequestBody, ChatResponseBody, Collection, ConversationSummary, StoredConversation } from '@/types/weave-api';

export function ChatApp() {
  const router = useRouter();

  const [bots, setBots] = useState<Bot[] | null>(null);
  const [botsError, setBotsError] = useState<MappedError | null>(null);
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [collectionsError, setCollectionsError] = useState<MappedError | null>(null);
  // The sidebar's Collections selection, now a real per-request FILTER
  // (see ChatRequestBody.collections's own docstring in
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

  // Guards against setting state from a turn the user has since abandoned
  // (switched bot / started a new one) while its request was in flight.
  const activeTurnRef = useRef<string | null>(null);

  // Re-fetches the history sidebar's list — called on mount and again
  // after anything that can change it (a turn creating/renaming a
  // conversation, or a delete) so the sidebar never goes stale.
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
      const [botsResult, collectionsResult] = await Promise.all([
        getJson<Bot[]>('/api/bots'),
        getJson<Collection[]>('/api/collections'),
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
  }

  function handleNewConversation() {
    activeTurnRef.current = null;
    setSending(false);
    setConversationId(null);
    setMessages([]);
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

      const outcome = await consumeChatStream(response.body, {
        onTrace: (trace) => {
          if (activeTurnRef.current === turnId) updateMessage(assistantId, (m) => ({ ...m, trace }));
        },
        onDelta: (text) => {
          if (activeTurnRef.current === turnId) updateMessage(assistantId, (m) => ({ ...m, content: m.content + text }));
        },
        onSources: (sources) => {
          if (activeTurnRef.current === turnId) updateMessage(assistantId, (m) => ({ ...m, sources }));
        },
      });

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

  async function handleSend() {
    const trimmed = draft.trim();
    if (!trimmed || !selectedBotId || sending) return;

    const turnId = newId();
    activeTurnRef.current = turnId;

    const userMsg = userMessage(trimmed);
    const assistantMsg = pendingAssistantMessage();
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setDraft('');
    setSending(true);

    const body = buildChatRequestBody({
      botId: selectedBotId,
      message: trimmed,
      conversationId,
      selectedCollections,
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
      // changed an existing one's title/updated_at — refresh regardless of
      // whether this turn was since abandoned, so the sidebar never shows
      // a stale list.
      void refreshConversations();
    }
  }

  // Loads one past conversation's full transcript (GET
  // /api/conversations/{id}) back into view — same abandon-safety pattern
  // as handleSelectBot/handleNewConversation above: any turn still in
  // flight for whatever was open before is abandoned first.
  async function handleSelectConversation(id: string) {
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
  }

  // Permanently removes one past conversation (DELETE
  // /api/conversations/{id}). A destructive, irreversible action — same
  // window.confirm discipline as this codebase's other delete actions
  // (e.g. Weave-Ingest's import-sync.tsx / imports/[id]/page.tsx).
  async function handleDeleteConversation(id: string) {
    if (!window.confirm('Diese Konversation und alle ihre Nachrichten unwiderruflich löschen?')) return;

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
    }
  }

  async function handleDeleteAllConversations() {
    if (!window.confirm('Den gesamten Chat-Verlauf mit allen Nachrichten unwiderruflich löschen?')) return;

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
  }

  const selectedBot = bots?.find((bot) => bot.id === selectedBotId) ?? null;

  return (
    <div className="flex h-screen w-full">
      <Sidebar
        bots={bots}
        botsError={botsError}
        selectedBotId={selectedBotId}
        onSelectBot={handleSelectBot}
        collections={collections}
        collectionsError={collectionsError}
        selectedCollections={selectedCollections}
        onToggleCollection={handleToggleCollection}
        onClearCollections={handleClearCollections}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
          <div>
            <h1 className="text-sm font-semibold">{selectedBot?.name ?? 'Kein Bot ausgewählt'}</h1>
            {conversationId ? (
              <p className="text-[11px] text-[var(--foreground-muted)]">Konversation: {conversationId}</p>
            ) : null}
          </div>
          <Button variant="outline" size="sm" onClick={handleNewConversation} disabled={messages.length === 0}>
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Neue Konversation
          </Button>
        </header>

        <MessageList messages={messages} />

        <Composer value={draft} onChange={setDraft} onSend={handleSend} disabled={sending} botSelected={!!selectedBotId} />
      </main>

      <HistoryPanel
        conversations={conversations}
        conversationsError={conversationsError}
        selectedConversationId={conversationId}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        onDeleteAllConversations={handleDeleteAllConversations}
      />
    </div>
  );
}
