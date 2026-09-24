'use client';

import { useRef } from 'react';
import { Bot as BotIcon, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScopePicker } from '@/components/chat/scope-picker';
import { useI18n } from '@/i18n/provider';
import type { MappedError } from '@/lib/errors';
import type { Bot, Collection } from '@/types/weave-api';

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled: boolean;
  bots: Bot[] | null;
  selectedBotId: string | null;
  onSelectBot: (botId: string) => void;
  collections: Collection[] | null;
  collectionsError?: MappedError | null;
  selectedCollections: string[];
  onToggleCollection: (slug: string) => void;
  onClearCollections: () => void;
}

/** Enter sends, Shift+Enter inserts a newline — IME composition (e.g. an
 * in-progress Japanese/Chinese conversion) is left alone so a plain Enter
 * used to confirm a candidate doesn't also send the message. */
export function Composer({
  value,
  onChange,
  onSend,
  disabled,
  bots,
  selectedBotId,
  onSelectBot,
  collections,
  collectionsError,
  selectedCollections,
  onToggleCollection,
  onClearCollections,
}: ComposerProps) {
  const { t } = useI18n();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const botSelected = !!selectedBotId;

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!disabled && value.trim()) onSend();
    }
  }

  const placeholder = botSelected ? t('chat.composer.placeholder') : t('chat.composer.placeholderNoBot');

  return (
    <div className="flex-none bg-gradient-to-t from-[var(--bg)] to-transparent px-3 pb-3 pt-2 sm:px-6">
      <div className="mx-auto max-w-[48rem] rounded-[var(--radius-card)] border border-[var(--line)] bg-[var(--surface)] shadow-sm focus-within:border-[var(--accent)] focus-within:ring-[3px] focus-within:ring-[var(--accent-soft)]">
        <label htmlFor="chat-composer-input" className="sr-only">
          {t('chat.composer.questionLabel')}
        </label>
        <textarea
          id="chat-composer-input"
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || !botSelected}
          placeholder={placeholder}
          rows={1}
          className="block max-h-40 min-h-[2.75rem] w-full resize-y rounded-[var(--radius-card)] border-0 bg-transparent px-4 pb-1 pt-3 text-sm outline-none placeholder:text-[var(--muted)] disabled:opacity-60"
        />

        <div className="flex flex-wrap items-center gap-2 px-2.5 pb-2.5 pt-1.5">
          <div className="relative inline-flex h-9 min-h-[40px] items-center gap-1.5 rounded-[var(--radius-pill)] border border-[var(--line-2)] bg-[var(--surface-2)] pl-2.5 pr-1.5 text-xs font-medium text-[var(--muted)] focus-within:border-[var(--accent)] focus-within:ring-[3px] focus-within:ring-[var(--accent-soft)] sm:min-h-0">
            <BotIcon className="h-3.5 w-3.5 text-[var(--accent)]" aria-hidden="true" />
            <select
              aria-label={t('chat.composer.assistantLabel')}
              value={selectedBotId ?? ''}
              onChange={(e) => onSelectBot(e.target.value)}
              disabled={!bots || bots.length === 0}
              className="max-w-[9rem] appearance-none truncate bg-transparent pr-3 font-semibold text-[var(--ink)] outline-none disabled:opacity-60"
            >
              {!bots || bots.length === 0 ? <option value="">{t('chat.composer.noBotAvailable')}</option> : null}
              {bots?.map((bot) => (
                <option key={bot.id} value={bot.id}>
                  {bot.name}
                </option>
              ))}
            </select>
          </div>

          <ScopePicker
            collections={collections}
            collectionsError={collectionsError}
            selectedCollections={selectedCollections}
            onToggleCollection={onToggleCollection}
            onClearCollections={onClearCollections}
          />

          <span className="hidden text-[11px] text-[var(--muted)] sm:ml-auto sm:inline">{t('chat.composer.sendHint')}</span>

          <Button
            type="button"
            onClick={onSend}
            disabled={disabled || !botSelected || !value.trim()}
            aria-label={t('chat.composer.send')}
            className="ml-auto h-10 w-10 flex-none rounded-[var(--radius-pill)] p-0 sm:ml-0"
          >
            <Send className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>
      <p className="mx-auto mt-1.5 max-w-[48rem] text-center text-[11px] text-[var(--muted)] sm:hidden">
        {t('chat.composer.sendHint')}
      </p>
    </div>
  );
}
