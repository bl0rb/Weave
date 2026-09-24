/**
 * Deterministic color mark for a Wissensbereiche card. The backend
 * (CollectionResponse) has no color field, so this picks a stable entry
 * from the --sp-1..--sp-6 palette (globals.css) by hashing the collection
 * id — same id always renders the same color without persisting anything.
 */
const PALETTE_SIZE = 6;

export function spaceColorVar(collectionId: string): string {
  let hash = 0;
  for (let index = 0; index < collectionId.length; index += 1) {
    hash = (hash * 31 + collectionId.charCodeAt(index)) >>> 0;
  }
  return `var(--sp-${(hash % PALETTE_SIZE) + 1})`;
}

/** First letter of the space name, uppercased — falls back to '?' for an empty name. */
export function spaceMark(name: string): string {
  return name.trim().charAt(0).toUpperCase() || '?';
}
