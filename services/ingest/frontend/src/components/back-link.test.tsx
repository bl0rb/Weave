// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { recordNavigation, resetNavigationHistory } from '@/lib/navigation-history';
import { BackLink } from './back-link';

const router = vi.hoisted(() => ({ back: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/', useSearchParams: () => new URLSearchParams() }));

beforeEach(() => { resetNavigationHistory(); router.back.mockReset(); });
afterEach(cleanup);

it('falls back to the fixed link without in-app history', () => {
  recordNavigation();
  render(<BackLink fallbackHref="/documents" fallbackLabel="← Dokumente" />);
  const link = screen.getByRole('link', { name: '← Dokumente' });
  expect(link.getAttribute('href')).toBe('/documents');
  expect(fireEvent.click(link)).toBe(true);
  expect(router.back).not.toHaveBeenCalled();
});

it('does not count replace-navigations as history', () => {
  window.history.replaceState({}, '', '/reviews/1');
  recordNavigation();
  render(<BackLink fallbackHref="/documents" fallbackLabel="← Dokumente" />);
  expect(screen.getByRole('link').textContent).toBe('← Dokumente');
});

it('uses router.back() with the generic label after an in-app push', async () => {
  window.history.pushState({}, '', '/jobs/1');
  recordNavigation();
  render(<BackLink fallbackHref="/documents" fallbackLabel="← Dokumente" />);
  const link = await screen.findByRole('link', { name: 'Zurück' });
  expect(fireEvent.click(link)).toBe(false);
  expect(router.back).toHaveBeenCalledTimes(1);
});

it('keeps the depth across replace and shows the fallback again after browser back', async () => {
  window.history.pushState({}, '', '/documents');
  window.history.replaceState({}, '', '/documents?stand=review');
  recordNavigation();
  render(<BackLink fallbackHref="/reviews" fallbackLabel="← Prüfen" />);
  expect(screen.getByRole('link').textContent).toBe('Zurück');
  cleanup();
  // Browser back restores the first entry's stamped state (depth 0).
  await new Promise(resolve => { window.addEventListener('popstate', resolve, { once: true }); window.history.back(); });
  recordNavigation();
  render(<BackLink fallbackHref="/reviews" fallbackLabel="← Prüfen" />);
  expect(screen.getByRole('link').textContent).toBe('← Prüfen');
});
