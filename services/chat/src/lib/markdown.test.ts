import { describe, expect, it } from 'vitest';
import { splitAnswerSources } from '@/lib/markdown';

describe('splitAnswerSources', () => {
  it.each(['Quellen', '**Quellen**', '### Quellen', 'Quellen:', '**Quellen:**', 'Sources'])(
    'splits at a "%s" heading',
    (heading) => {
      const split = splitAnswerSources(`Antwort.\n\n${heading}\n- [source 1] a.pdf\n- [source 2] b.pdf`);
      expect(split.body).toBe('Antwort.');
      expect(split.sources).toBe('- [source 1] a.pdf\n- [source 2] b.pdf');
      expect(split.sourcesLabel?.toLowerCase()).toBe(heading.replace(/[#*:\s]/g, '').toLowerCase());
    }
  );

  it('ignores a heading without text before or after it', () => {
    expect(splitAnswerSources('Quellen\n- a.pdf').sourcesLabel).toBeNull();
    expect(splitAnswerSources('Antwort.\n\nQuellen').sourcesLabel).toBeNull();
  });

  it('ignores a heading inside a code fence and words in running text', () => {
    expect(splitAnswerSources('Antwort.\n```\nQuellen\nx\n```').sourcesLabel).toBeNull();
    expect(splitAnswerSources('Die Quellen sind unten.\nmehr').sourcesLabel).toBeNull();
  });
});
