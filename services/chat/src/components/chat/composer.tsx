'use client';

import { useRef } from 'react';
import { ArrowUp, Bot as BotIcon, ChevronDown, Cpu } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScopePicker } from '@/components/chat/scope-picker';
import { useI18n } from '@/i18n/provider';
import type { MappedError } from '@/lib/errors';
import { cn } from '@/lib/utils';
import type { Bot, Collection, LlmEndpoint } from '@/types/weave-api';

// Borderless "ghost" pills for the assistant/model selects -- the composer
// card itself is the frame, so the controls stay visually quiet until hovered.
const PILL_CLASS =
  'relative inline-flex h-9 min-h-[40px] items-center gap-1.5 rounded-[var(--radius-pill)] px-2.5 text-[13px] font-medium text-[var(--muted)] transition-colors hover:bg-[var(--hover)] focus-within:bg-[var(--hover)] focus-within:ring-[3px] focus-within:ring-[var(--accent-soft)] sm:min-h-0';
const SELECT_CLASS =
  'max-w-[10rem] cursor-pointer appearance-none truncate bg-transparent font-semibold text-[var(--ink)] outline-none disabled:cursor-default disabled:opacity-60';

interface ComposerProps {
  /** `hero`: the empty-conversation start screen (centered, taller input);
   * `docked` (default): pinned below the message list. */
  variant?: 'hero' | 'docked';
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled: boolean;
  bots: Bot[] | null;
  selectedBotId: string | null;
  onSelectBot: (botId: string) => void;
  llmEndpoints?: LlmEndpoint[];
  selectedEndpointId?: string | null;
  onSelectEndpoint?: (endpointId: string) => void;
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
  variant = 'docked',
  value,
  onChange,
  onSend,
  disabled,
  bots,
  selectedBotId,
  onSelectBot,
  llmEndpoints = [],
  selectedEndpointId = null,
  onSelectEndpoint,
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
  const hero = variant === 'hero';

  return (
    <div className={cn('flex-none px-3 sm:px-6', hero ? 'pb-2' : 'bg-gradient-to-t from-[var(--bg)] to-transparent pb-3 pt-2')}>
      <div className="mx-auto max-w-[48rem] rounded-[1.375rem] border border-[var(--line)] bg-[var(--surface)] shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_32px_-16px_rgb(0_0_0/0.18)] transition-[border-color,box-shadow] focus-within:border-[var(--line-2)] focus-within:shadow-[0_1px_2px_rgb(0_0_0/0.05),0_16px_40px_-16px_rgb(0_0_0/0.25)]">
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
          rows={hero ? 3 : 1}
          className={cn(
            'block max-h-60 w-full resize-none field-sizing-content rounded-t-[1.375rem] border-0 bg-transparent px-5 pb-1 pt-4 text-[15px] leading-relaxed outline-none placeholder:text-[var(--muted)] disabled:opacity-60',
            hero ? 'min-h-[5.5rem]' : 'min-h-[3.25rem]'
          )}
        />

        <div className="flex flex-wrap items-center gap-1 px-2.5 pb-2.5 pt-1">
          <ScopePicker
            collections={collections}
            collectionsError={collectionsError}
            selectedCollections={selectedCollections}
            onToggleCollection={onToggleCollection}
            onClearCollections={onClearCollections}
          />

          <div className={cn(PILL_CLASS, 'ml-auto')}>
            <BotIcon className="h-4 w-4 text-[var(--accent)]" aria-hidden="true" />
            <select
              aria-label={t('chat.composer.assistantLabel')}
              value={selectedBotId ?? ''}
              onChange={(e) => onSelectBot(e.target.value)}
              disabled={!bots || bots.length === 0}
              className={SELECT_CLASS}
            >
              {!bots || bots.length === 0 ? <option value="">{t('chat.composer.noBotAvailable')}</option> : null}
              {bots?.map((bot) => (
                <option key={bot.id} value={bot.id}>
                  {bot.name}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none h-3 w-3 flex-none" aria-hidden="true" />
          </div>

          {llmEndpoints.length > 1 ? (
            <div className={PILL_CLASS}>
              <Cpu className="h-4 w-4 text-[var(--accent)]" aria-hidden="true" />
              <select
                aria-label={t('chat.composer.modelLabel')}
                value={selectedEndpointId ?? llmEndpoints[0].id}
                onChange={(e) => onSelectEndpoint?.(e.target.value)}
                className={SELECT_CLASS}
              >
                {llmEndpoints.map((endpoint) => (
                  <option key={endpoint.id} value={endpoint.id} title={endpoint.model}>
                    {endpoint.name}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none h-3 w-3 flex-none" aria-hidden="true" />
            </div>
          ) : null}

          <Button
            type="button"
            onClick={onSend}
            disabled={disabled || !botSelected || !value.trim()}
            aria-label={t('chat.composer.send')}
            className="ml-1 h-10 w-10 flex-none rounded-[var(--radius-pill)] p-0 disabled:bg-[var(--line-2)] disabled:text-[var(--muted)] disabled:opacity-100"
          >
            <ArrowUp className="h-5 w-5" aria-hidden="true" />
          </Button>
        </div>
      </div>
      <p className="mx-auto mt-2 max-w-[48rem] text-center text-[11px] text-[var(--muted)]">{t('chat.composer.sendHint')}</p>
    </div>
  );
}
