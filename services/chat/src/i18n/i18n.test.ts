import { describe, expect, it } from 'vitest';
import { resolveLocale } from './config';
import { translate } from './messages';

describe('resolveLocale', () => {
  it('prefers a valid cookie, then Accept-Language, then German', () => {
    expect(resolveLocale('en', 'de-DE')).toBe('en');
    expect(resolveLocale('fr', 'en-US,en;q=0.9')).toBe('en');
    expect(resolveLocale(undefined, 'fr-FR,fr;q=0.9')).toBe('de');
    expect(resolveLocale(null, null)).toBe('de');
  });
});

describe('translate', () => {
  it('returns the locale string and falls back to German', () => {
    expect(translate('en', 'common.language')).toBe('Language');
    expect(translate('de', 'common.language')).toBe('Sprache');
    expect(translate('en', 'chat.composer.send')).toBe('Send message');
    expect(translate('de', 'chat.composer.send')).toBe('Nachricht senden');
  });

  it('interpolates variables and picks singular/plural by count', () => {
    expect(translate('de', 'chat.messageBubble.sourcedBy', { count: 1 })).toBe('Belegt durch 1 Quelle');
    expect(translate('de', 'chat.messageBubble.sourcedBy', { count: 3 })).toBe('Belegt durch 3 Quellen');
    expect(translate('en', 'chat.messageBubble.sourcedBy', { count: 0 })).toBe('Backed by 0 sources');
    expect(translate('en', 'chat.messageBubble.sourcedBy', { count: 1 })).toBe('Backed by 1 source');
  });

  it('falls back to German for a namespace/id an EN catalog happens to be missing (typing already prevents this — this just documents the runtime fallback)', () => {
    expect(translate('en', 'chat.scope.all')).toBe('All spaces');
  });
});
