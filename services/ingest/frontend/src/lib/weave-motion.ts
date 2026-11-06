import { useCallback, useState, useSyncExternalStore } from 'react';

/**
 * How the Weave mark moves while something is busy: 'weave' -- the weft
 * rows unweave and shuttle back in. The only motion: the mark itself never
 * turns, flips or sways. Kept as a list (and a picker) so the components
 * that ask for "the next motion" stay unchanged. Mirrors bl0rb/WeaveDesktop's
 * ui/common.js and Weave-Chat's lib/weave-motion.ts; the canonical mark and keyframes live in
 * packages/weave-mark.
 */
export const WEAVE_MOTIONS = ['weave'] as const;
export type WeaveMotion = (typeof WEAVE_MOTIONS)[number];

export function nextWeaveMotion(): WeaveMotion {
  return 'weave';
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
