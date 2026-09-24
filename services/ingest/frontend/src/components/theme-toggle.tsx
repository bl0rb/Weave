'use client';

import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { useI18n } from '@/i18n/provider';

type Theme = 'light' | 'dark';
const STORAGE_KEY = 'weave-ingest-theme';

export function ThemeToggle() {
  const { t } = useI18n();
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(stored === 'dark' || (stored !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light');
  }, []);

  useEffect(() => {
    if (!theme) return;
    document.documentElement.classList.remove('light', 'dark');
    document.documentElement.classList.add(theme);
    window.localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  if (!theme) return null;
  const dark = theme === 'dark';
  return (
    <button
      type="button"
      aria-label={dark ? t('portal.themeToggle.enableLight') : t('portal.themeToggle.enableDark')}
      title={dark ? t('portal.themeToggle.light') : t('portal.themeToggle.dark')}
      onClick={() => setTheme(dark ? 'light' : 'dark')}
      className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-slate-700"
    >
      {dark ? <Sun className="h-4 w-4" aria-hidden="true" /> : <Moon className="h-4 w-4" aria-hidden="true" />}
    </button>
  );
}