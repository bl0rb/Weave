'use client';

import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';

const STORAGE_KEY = 'weave-chat-theme';

type Theme = 'light' | 'dark';

function readStoredTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    // Private window / storage disabled — fall back to the OS setting.
    return null;
  }
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** Manual light/dark override on top of the OS default (see globals.css:
 * `.dark`/`.light` classes on `<html>` win over `prefers-color-scheme`).
 * Persisted per-browser in localStorage — a per-viewer convenience, not
 * app state, so it is fine that it never reaches the server. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    // Deliberately a mount-only effect, not a lazy useState initializer:
    // this reads localStorage/matchMedia, which do not exist during SSR.
    // The initial `null` render has to match what the server rendered
    // (nothing — see the early return below) to avoid a hydration
    // mismatch; only once mounted in a real browser can the actual
    // theme be known, which is exactly what an effect is for.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(readStoredTheme() ?? (systemPrefersDark() ? 'dark' : 'light'));
  }, []);

  useEffect(() => {
    if (!theme) return;
    document.documentElement.classList.remove('light', 'dark');
    document.documentElement.classList.add(theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Nothing to persist to — the class is still applied for this load.
    }
  }, [theme]);

  if (!theme) return null;

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      aria-label={theme === 'dark' ? 'Helles Farbschema aktivieren' : 'Dunkles Farbschema aktivieren'}
      title={theme === 'dark' ? 'Helles Farbschema' : 'Dunkles Farbschema'}
      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
    >
      {theme === 'dark' ? <Sun className="h-4 w-4" aria-hidden="true" /> : <Moon className="h-4 w-4" aria-hidden="true" />}
    </Button>
  );
}
