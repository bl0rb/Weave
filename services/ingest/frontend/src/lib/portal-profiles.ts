import { DEFAULT_LOCALE, type Locale } from '@/i18n/config';
import { translate } from '@/i18n/messages';

export type ProcessingProfile = { value: string; label: string; description: string; kind?: string };
export type PortalProfile = ProcessingProfile & { kind: 'ocr' | 'vl' };

// Keep the business-facing choices small, using only real server capabilities.
function presets(locale: Locale) {
  return [
    {
      value: 'ppocrv6_tiny_structurev3',
      label: translate(locale, 'portal.profiles.standard.label'),
      description: translate(locale, 'portal.profiles.standard.description'),
    },
    {
      value: 'ppocrv6_medium_structurev3',
      label: translate(locale, 'portal.profiles.thorough.label'),
      description: translate(locale, 'portal.profiles.thorough.description'),
    },
  ];
}

export function portalProfiles(available: ProcessingProfile[], locale: Locale = DEFAULT_LOCALE): PortalProfile[] {
  const local: PortalProfile[] = presets(locale).filter(preset => available.some(profile => profile.value === preset.value))
    .map(preset => ({ ...preset, kind: 'ocr' }));
  const configured: PortalProfile[] = available.filter(profile => profile.kind === 'vl' && profile.value.startsWith('vl:'))
    .map(profile => ({
      ...profile,
      kind: 'vl',
      label: profile.label.replace(/^VL:\s*/, ''),
      description: `${profile.description.replace(/ — vision-language connection$/, '')}${translate(locale, 'portal.profiles.vlDescriptionSuffix')}`,
    }));
  return [...local, ...configured];
}
