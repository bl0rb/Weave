// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { LOCALE_COOKIE, resolveLocale } from './config';
import { translate } from './messages';

describe('resolveLocale', () => {
  it('prefers a valid cookie, then Accept-Language, then German', () => {
    expect(resolveLocale('en', 'de-DE')).toBe('en');
    expect(resolveLocale('fr', 'en-US,en;q=0.9')).toBe('en');
    expect(resolveLocale(undefined, 'fr-FR,fr;q=0.9')).toBe('de');
    expect(resolveLocale(null, null)).toBe('de');
  });

  it('falls back the same way for a missing cookie (the Auto preference)', () => {
    expect(resolveLocale(null, 'en-US,en;q=0.9')).toBe('en');
  });
});

describe('LOCALE_COOKIE', () => {
  it('is named identically to services/chat, so a switch in one app carries over to the other', () => {
    expect(LOCALE_COOKIE).toBe('weave_lang');
  });
});

describe('translate', () => {
  it('returns the locale string and falls back to German', () => {
    expect(translate('en', 'common.save')).toBe('Save');
    expect(translate('de', 'common.save')).toBe('Speichern');
  });

  it('interpolates variables and picks singular/plural by count', () => {
    expect(translate('de', 'common.documents.count', { count: 1 })).toBe('1 Dokument');
    expect(translate('de', 'common.documents.count', { count: 3 })).toBe('3 Dokumente');
    expect(translate('en', 'common.documents.count', { count: 0 })).toBe('0 documents');
  });
});

describe('useI18n formatDate', () => {
  it('renders a dash for missing or invalid dates instead of throwing', async () => {
    const { renderHook } = await import('@testing-library/react');
    const { useI18n } = await import('./provider');
    const { result } = renderHook(() => useI18n());
    expect(result.current.formatDate(null)).toBe('–');
    expect(result.current.formatDate(undefined)).toBe('–');
    expect(result.current.formatDate('not a date')).toBe('–');
    expect(result.current.formatDate('2026-09-24T10:00:00Z')).toMatch(/2026/);
  });
});

describe('useI18n preference', () => {
  it('defaults to Auto without a provider', async () => {
    const { renderHook } = await import('@testing-library/react');
    const { useI18n } = await import('./provider');
    const { result } = renderHook(() => useI18n());
    expect(result.current.preference).toBe('auto');
  });
});
