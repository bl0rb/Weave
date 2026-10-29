import { useCallback, useState, useSyncExternalStore } from 'react';

/**
 * The four ways the Weave mark moves while something is busy -- 'weave'
 * (the weft rows unweave and shuttle back in), 'turn' (the desktop app's
 * half turn; the mark is symmetric, so 180° looks like 0°), 'flip' (a half
 * turn around the vertical axis, same symmetry) and 'shuttle' (the mark sways
 * while the weft rows slide through like a loom's shuttle). Every start
 * picks one at random, never the one that ran last, remembered across page
 * loads through localStorage so a reload cannot repeat it either. Mirrors
 * bl0rb/WeaveDesktop's ui/common.js and Weave-Chat's lib/weave-motion.ts; the
 * canonical mark and keyframes live in packages/weave-mark.
 */
export const WEAVE_MOTIONS = ['weave', 'turn', 'flip', 'shuttle'] as const;
export type WeaveMotion = (typeof WEAVE_MOTIONS)[number];

const STORAGE_KEY = 'weave-motion-last';
let memoryLast: WeaveMotion | null = null;

function isMotion(value: unknown): value is WeaveMotion {
  return typeof value === 'string' && (WEAVE_MOTIONS as readonly string[]).includes(value);
}

export function nextWeaveMotion(random: () => number = Math.random): WeaveMotion {
  let last: WeaveMotion | null = memoryLast;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isMotion(stored)) last = stored;
  } catch {
    // private mode / blocked storage: the in-memory memory still prevents repeats
  }
  const candidates = WEAVE_MOTIONS.filter((motion) => motion !== last);
  const next = candidates[Math.min(candidates.length - 1, Math.floor(random() * candidates.length))];
  memoryLast = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // ignore
  }
  return next;
}

const noopSubscribe = () => () => {};

export interface WeaveMotionState {
  /** The motion to run, undefined while nothing moves. */
  motion: WeaveMotion | undefined;
  /** Whether the mark runs a short self-started round (a click on it). */
  kicked: boolean;
  /** Pick the next motion now -- and, when idle, run one short round of it. */
  cycle: () => void;
  /** The current round has ended (animationend): clears a self-started round. */
  settle: () => void;
}

/**
 * Picks a motion once when `active` turns true and keeps it until `active`
 * turns false again -- so a loop that settles into its finite rounds keeps
 * moving the same way instead of restarting. Picked on the client only
 * (never during server rendering or hydration), so server and client
 * markup agree; the pick itself is the "adjust state while rendering"
 * pattern, not an effect.
 */
export function useWeaveMotion(active: boolean): WeaveMotionState {
  const isClient = useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
  const [picked, setPicked] = useState<{ active: boolean; motion?: WeaveMotion; kicked: boolean }>({
    active: false,
    kicked: false,
  });
  if (isClient && picked.active !== active) {
    setPicked({ active, motion: active ? nextWeaveMotion() : picked.kicked ? picked.motion : undefined, kicked: picked.kicked && !active });
  }
  const cycle = useCallback(() => {
    setPicked((current) => ({ active: current.active, motion: nextWeaveMotion(), kicked: !current.active }));
  }, []);
  const settle = useCallback(() => {
    setPicked((current) => (current.kicked ? { active: current.active, motion: current.active ? current.motion : undefined, kicked: false } : current));
  }, []);
  return { motion: picked.motion, kicked: picked.kicked, cycle, settle };
}
