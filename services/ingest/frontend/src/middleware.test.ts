import { NextRequest } from 'next/server';
import { afterEach, expect, it, vi } from 'vitest';

import { middleware } from './middleware';

function policy(url: string, headers?: Record<string, string>): Map<string, string> {
  const csp = middleware(new NextRequest(url, { headers })).headers.get('Content-Security-Policy') ?? '';
  return new Map(csp.split('; ').map((part) => {
    const [name, ...values] = part.split(' ');
    return [name, values.join(' ')] as [string, string];
  }));
}

afterEach(() => vi.unstubAllEnvs());

it('allows images (branding assets, favicon) from the configured API origin', () => {
  vi.stubEnv('WEAVE_INGEST_PUBLIC_API_URL', 'https://api.example/base/');
  const csp = policy('https://portal.example/login');
  expect(csp.get('img-src')).toBe("'self' data: blob: https://api.example");
  expect(csp.get('connect-src')).toBe("'self' https://api.example");
});

it('names the derived :8000 origin when no API URL is configured', () => {
  vi.stubEnv('WEAVE_INGEST_PUBLIC_API_URL', '');
  expect(policy('http://portal.example/', { host: 'portal.example' }).get('img-src')).toBe("'self' data: blob: http://portal.example:8000");
});
