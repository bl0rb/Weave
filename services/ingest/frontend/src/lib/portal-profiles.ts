export type ProcessingProfile = { value: string; label: string; description: string; kind?: string };
export type PortalProfile = ProcessingProfile & { kind: 'ocr' | 'vl' };

// Keep the business-facing choices small, using only real server capabilities.
const PRESETS = [
  {
    value: 'ppocrv6_tiny_structurev3',
    label: 'Standard – schnell',
    description: 'Für den Einstieg und typische Dokumente. Erkennt Text, Layout und Tabellen mit einem kleinen Modell.',
  },
  {
    value: 'ppocrv6_medium_structurev3',
    label: 'Gründlich – komplexe Dokumente',
    description: 'Für anspruchsvolle Layouts und Tabellen. Verwendet größere Modelle und benötigt mehr Rechenzeit.',
  },
];

export function portalProfiles(available: ProcessingProfile[]): PortalProfile[] {
  const local: PortalProfile[] = PRESETS.filter(preset => available.some(profile => profile.value === preset.value))
    .map(preset => ({ ...preset, kind: 'ocr' }));
  const configured: PortalProfile[] = available.filter(profile => profile.kind === 'vl' && profile.value.startsWith('vl:'))
    .map(profile => ({
      ...profile,
      kind: 'vl',
      label: profile.label.replace(/^VL:\s*/, ''),
      description: `${profile.description.replace(/ — vision-language connection$/, '')}. Verarbeitet die Datei über die von der Administration eingerichtete KI-Verbindung.`,
    }));
  return [...local, ...configured];
}
