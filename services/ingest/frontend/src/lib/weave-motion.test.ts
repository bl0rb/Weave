// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';

import { WEAVE_MOTIONS, nextWeaveMotion } from './weave-motion';

describe('nextWeaveMotion', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('never repeats the motion that ran last, across reloads via storage', () => {
    window.localStorage.setItem('weave-motion-last', 'turn');
    const first = nextWeaveMotion();
    expect(first).not.toBe('turn');
    expect(window.localStorage.getItem('weave-motion-last')).toBe(first);
    const picks = [first, ...Array.from({ length: 40 }, () => nextWeaveMotion())];
    for (let i = 1; i < picks.length; i += 1) expect(picks[i]).not.toBe(picks[i - 1]);
    expect(new Set(picks)).toEqual(new Set(WEAVE_MOTIONS));
  });

  it('is driven by the random source and keeps every motion reachable', () => {
    window.localStorage.setItem('weave-motion-last', 'weave');
    expect(nextWeaveMotion(() => 0)).toBe('turn');
    expect(nextWeaveMotion(() => 0.999)).toBe('shuttle');
    expect(nextWeaveMotion(() => 0)).toBe('weave');
  });
});
