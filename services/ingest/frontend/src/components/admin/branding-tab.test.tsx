// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { ApiError, apiJson } from '@/lib/api';
import { BrandingTab, type BrandingAdmin } from './branding-tab';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const branding = vi.hoisted(() => ({ logoUrl: null, busyUrl: null, refresh: vi.fn(async () => {}) }));
vi.mock('@/components/branding-provider', () => ({ useBranding: () => branding }));

const api = vi.mocked(apiJson);
const SHA = 'ef'.repeat(32);
const palette = {
  light: { accent: '#aabbcc', accent_hover: '#919fad', accent_ink: '#4d5760', accent_soft: '#f4f6f8', on_accent: '#14221d' },
  dark: { accent: '#aabbcc', accent_hover: '#b6c4d2', accent_ink: '#e3e8ee', accent_soft: '#3a3e44', on_accent: '#0c1511' },
};
const empty: BrandingAdmin = { primary_color: null, accent_color: null, palette: null, logo: null, busy: null, updated_at: null, managed: false };
const logo = { content_type: 'image/svg+xml', sha256: SHA, size_bytes: 2048, url: `/api/v1/branding/assets/logo?v=${SHA.slice(0, 16)}` };

beforeEach(() => {
  api.mockReset();
  branding.refresh.mockClear();
});
afterEach(cleanup);

function putCalls(path: string) {
  return api.mock.calls.filter(([callPath, init]) => callPath === path && init?.method === 'PUT');
}

it('saves normalised hex colours, shows the saved palette and restyles the portal', async () => {
  const saved = { ...empty, primary_color: '#aabbcc', palette, managed: true };
  api.mockImplementation(async (path, init) => (init?.method === 'PUT' ? saved : empty));
  render(<BrandingTab />);
  expect(await screen.findByText('Es gelten die Standardfarben.')).toBeTruthy();

  fireEvent.change(screen.getByRole('textbox', { name: 'Primärfarbe' }), { target: { value: '#ABC' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));

  await waitFor(() => expect(putCalls('/api/v1/auth/admin/branding')).toHaveLength(1));
  expect(JSON.parse(putCalls('/api/v1/auth/admin/branding')[0][1]?.body as string)).toEqual({ primary_color: '#aabbcc', accent_color: null });
  expect(await screen.findByText('Gespeichert')).toBeTruthy();
  expect(branding.refresh).toHaveBeenCalledWith(saved);
  expect(screen.getByTestId('branding-palette-light').textContent).toContain('#919fad');
  expect(screen.getByTestId('branding-palette-dark').textContent).toContain('#3a3e44');
});

it('keeps the swatch and the text field in sync, and "Standard" resets to the default', async () => {
  api.mockImplementation(async (path, init) => (init?.method === 'PUT' ? empty : { ...empty, accent_color: '#123456' }));
  render(<BrandingTab />);
  const accent = await screen.findByRole('textbox', { name: 'Akzentfarbe' }) as HTMLInputElement;
  expect(accent.value).toBe('#123456');
  const swatch = screen.getByLabelText('Primärfarbe auswählen') as HTMLInputElement;
  expect(swatch.value).toBe('#1d6a53');
  fireEvent.change(swatch, { target: { value: '#ff0000' } });
  expect((screen.getByRole('textbox', { name: 'Primärfarbe' }) as HTMLInputElement).value).toBe('#ff0000');

  fireEvent.click(screen.getByRole('button', { name: 'Akzentfarbe: Standard verwenden' }));
  expect(accent.value).toBe('');
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(putCalls('/api/v1/auth/admin/branding')).toHaveLength(1));
  expect(JSON.parse(putCalls('/api/v1/auth/admin/branding')[0][1]?.body as string)).toEqual({ primary_color: '#ff0000', accent_color: null });
});

it('checks the hex format before saving', async () => {
  api.mockResolvedValue(empty);
  render(<BrandingTab />);
  fireEvent.change(await screen.findByRole('textbox', { name: 'Primärfarbe' }), { target: { value: 'grün' } });
  expect(screen.getByText('Farbe als Hex-Code angeben, z. B. #1d6a53.')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Speichern' }) as HTMLButtonElement).disabled).toBe(true);
  expect(putCalls('/api/v1/auth/admin/branding')).toHaveLength(0);
});

it('shows the backend error when saving fails', async () => {
  api.mockImplementation(async (path, init) => {
    if (init?.method === 'PUT') throw new ApiError(422, 'Farbe als Hex-Code angeben, z. B. #1d6a53.');
    return empty;
  });
  render(<BrandingTab />);
  fireEvent.change(await screen.findByRole('textbox', { name: 'Primärfarbe' }), { target: { value: '#123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Farbe als Hex-Code angeben, z. B. #1d6a53.');
  expect(branding.refresh).not.toHaveBeenCalled();
});

it('uploads a logo as multipart field "file"', async () => {
  const uploaded = { ...empty, logo, managed: true };
  api.mockImplementation(async (path, init) => (init?.method === 'PUT' ? uploaded : empty));
  render(<BrandingTab />);
  const input = await screen.findByLabelText('Datei für Logo') as HTMLInputElement;
  expect(input.accept).toBe('.svg,.gif,.png,image/svg+xml,image/gif,image/png');
  const file = new File(['<svg xmlns="http://www.w3.org/2000/svg"/>'], 'logo.svg', { type: 'image/svg+xml' });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: 'Logo hochladen' }));

  await waitFor(() => expect(putCalls('/api/v1/auth/admin/branding/assets/logo')).toHaveLength(1));
  const body = putCalls('/api/v1/auth/admin/branding/assets/logo')[0][1]?.body as FormData;
  expect(body).toBeInstanceOf(FormData);
  expect((body.get('file') as File).name).toBe('logo.svg');
  expect(await screen.findByText('Hochgeladen')).toBeTruthy();
  expect(branding.refresh).toHaveBeenCalledWith(uploaded);
  expect(screen.getByText('Eigene Datei: SVG, 2 KiB')).toBeTruthy();
  const preview = document.querySelector('img');
  expect(preview?.getAttribute('src')).toMatch(new RegExp(`/api/v1/branding/assets/logo\\?v=${SHA.slice(0, 16)}$`));
});

it('rejects files over 1 MiB before uploading', async () => {
  api.mockResolvedValue(empty);
  render(<BrandingTab />);
  const big = new File([new Uint8Array(1024 * 1024 + 1)], 'busy.gif', { type: 'image/gif' });
  fireEvent.change(await screen.findByLabelText('Datei für Lade-Animation'), { target: { files: [big] } });
  fireEvent.click(screen.getByRole('button', { name: 'Lade-Animation hochladen' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Die Datei ist größer als 1 MiB.');
  expect(putCalls('/api/v1/auth/admin/branding/assets/busy')).toHaveLength(0);
});

it('shows the backend error of a rejected upload', async () => {
  api.mockImplementation(async (path, init) => {
    if (init?.method === 'PUT') throw new ApiError(415, 'Nur SVG, GIF oder PNG.');
    return empty;
  });
  render(<BrandingTab />);
  fireEvent.change(await screen.findByLabelText('Datei für Logo'), { target: { files: [new File(['x'], 'logo.bmp')] } });
  fireEvent.click(screen.getByRole('button', { name: 'Logo hochladen' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Nur SVG, GIF oder PNG.');
  expect(branding.refresh).not.toHaveBeenCalled();
});

it('removes an asset with DELETE', async () => {
  api.mockImplementation(async (path, init) => (init?.method === 'DELETE' ? empty : { ...empty, logo, managed: true }));
  render(<BrandingTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Logo entfernen' }));
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/v1/auth/admin/branding/assets/logo', { method: 'DELETE' }));
  expect(await screen.findByText('Entfernt')).toBeTruthy();
  expect(branding.refresh).toHaveBeenCalledWith(empty);
  expect(screen.queryByRole('button', { name: 'Logo entfernen' })).toBeNull();
  expect(screen.getAllByText('Standard: das Weave-Zeichen')).toHaveLength(2);
});
