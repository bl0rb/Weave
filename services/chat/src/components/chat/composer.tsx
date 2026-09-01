'use client';

import { useRef } from 'react';
import { Send } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled: boolean;
  botSelected: boolean;
}

/** Enter sends, Shift+Enter inserts a newline — IME composition (e.g. an
 * in-progress Japanese/Chinese conversion) is left alone so a plain Enter
 * used to confirm a candidate doesn't also send the message. */
export function Composer({ value, onChange, onSend, disabled, botSelected }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!disabled && value.trim()) onSend();
    }
  }

  const placeholder = botSelected ? 'Nachricht schreiben…' : 'Bitte zuerst einen Bot auswählen';

  return (
    <div className="border-t border-[var(--border)] bg-[var(--surface)] p-3">
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || !botSelected}
          placeholder={placeholder}
          rows={1}
          className="max-h-40 min-h-[2.5rem] flex-1 resize-y rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm outline-none placeholder:text-[var(--foreground-muted)] focus-visible:border-[var(--accent)] disabled:opacity-60"
        />
        <Button
          type="button"
          onClick={onSend}
          disabled={disabled || !botSelected || !value.trim()}
          aria-label="Nachricht senden"
        >
          <Send className="h-4 w-4" aria-hidden="true" />
          Senden
        </Button>
      </div>
      <p className="mt-1.5 text-[11px] text-[var(--foreground-muted)]">
        Enter zum Senden · Shift+Enter für einen Zeilenumbruch
      </p>
    </div>
  );
}
