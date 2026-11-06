// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import { WEAVE_MOTIONS, nextWeaveMotion } from './weave-motion';

describe('nextWeaveMotion', () => {
  it('only ever weaves -- no turning, flipping or swaying', () => {
    expect(WEAVE_MOTIONS).toEqual(['weave']);
    expect(Array.from({ length: 5 }, () => nextWeaveMotion())).toEqual(['weave', 'weave', 'weave', 'weave', 'weave']);
  });
});
