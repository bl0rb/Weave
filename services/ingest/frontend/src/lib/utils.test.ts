import { expect, it } from 'vitest';
import { firstName } from './utils';

it('uses the first name of a long login', () => {
  expect(firstName('mathias.werk@example.com')).toBe('Mathias');
  expect(firstName('mathias_werk')).toBe('Mathias');
});

it('keeps the local part when the first token is only an initial', () => {
  expect(firstName('m.werk@firma.de')).toBe('m.werk');
});

it('keeps a login without separators', () => {
  expect(firstName('S12345')).toBe('S12345');
});
