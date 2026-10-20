"""WebVTT meeting transcript -> Markdown (the `.vtt` upload path).

Teams, Zoom, Webex and Google Meet all export a meeting transcript as
WebVTT: a `WEBVTT` header, optional NOTE/STYLE/REGION blocks and one cue
per caption, each cue a timing line and one or more text lines, with the
speaker carried either as a `<v Name>` voice tag (Teams, Meet) or an
inline `Name: text` prefix (Zoom). Fed to RAG verbatim, such a file is
mostly noise: hundreds of 2-4 second snippets, repeated roll-up lines,
timestamps on every line and a scattering of system boilerplate
("Recording has started", "[inaudible]"). This module turns it into
what a reader -- and a retriever -- actually wants from a meeting:

1. Parse the cues (stdlib only; malformed timing lines are skipped, not
   fatal, because exported transcripts are routinely hand-edited).
2. Drop boilerplate: NOTE/STYLE/REGION blocks, cues matching the
   platform system messages in `_BOILERPLATE_PATTERNS`, and bracketed
   non-speech annotations like `[Musik]` / `(unverständlich)`.
3. Merge consecutive cues of the same speaker into one turn, collapsing
   the duplicated/overlapping lines that roll-up captions produce. A
   silence longer than `_TURN_GAP_SECONDS` also starts a new turn, so a
   speaker-less transcript still breaks into readable paragraphs.
4. Render: a short header (speakers, duration), then one paragraph per
   turn as `**Speaker** [hh:mm:ss]: text`. One timestamp per turn keeps
   the citation value without drowning the text in numbers.

Only the body is produced here; the pipeline's own RAG frontmatter is
prepended by app/services/paddle_service.py, same as for .md/.xml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TIMESTAMP_RE = re.compile(r'(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{3})')
_TIMING_LINE_RE = re.compile(
    r'^\s*((?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3})'
)
_VOICE_TAG_RE = re.compile(r'<v(?:\.[^\s>]*)?\s+([^>]*)>', re.IGNORECASE)
_ANY_TAG_RE = re.compile(r'</?[A-Za-z][^>]*>|<\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}>')
# Zoom-style inline speaker prefix: at most four words, starting with a
# letter. Only promoted to a speaker when the same prefix recurs across the
# transcript (see parse_vtt) -- "Hinweis: morgen ist Abgabe" must stay text.
_INLINE_SPEAKER_RE = re.compile(r'^([^\W\d_][^:\n]{0,40}?):\s+(?=\S)')
# Non-speech annotations: [Musik], [inaudible], (unverständlich), [Applaus].
# Square brackets are caption convention and always stripped; parentheses
# only when their content is a known annotation, since speakers use
# parentheses for real asides.
_BRACKET_ANNOTATION_RE = re.compile(r'\[[^\]]{1,40}\]')
_PAREN_ANNOTATION_RE = re.compile(
    r'\((?:unverständlich|unhörbar|inaudible|crosstalk|laughter|laughs|lacht|lachen|music|musik|'
    r'applause|applaus|pause|silence|stille|noise|geräusch(?:e)?|unintelligible)\)',
    re.IGNORECASE,
)
_INLINE_SPEAKER_MIN_CUES = 2
# System messages the meeting platforms inject into the transcript itself.
# Matched against the full cue text (case-insensitive); a cue that is only
# such a message is dropped. Keep the list conservative: a false positive
# deletes a real sentence.
_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r'^(?:the )?(?:recording|transcription|transcript|aufzeichnung|aufnahme|transkription)'
        r'(?: (?:has|is|wurde|wird|ist))? ?(?:started|stopped|begun|in progress|gestartet|beendet|gestoppt|läuft|aktiv)\b',
        r'^this (?:meeting|call) is being recorded\b',
        r'^(?:diese(?:s|r)? )?(?:besprechung|meeting|anruf) wird aufgezeichnet\b',
        r'^(?:das )?meeting wird (?:jetzt )?(?:aufgezeichnet|transkribiert)\b',
        r'^.{0,40}\b(?:has|hat|ist) (?:joined|left) the (?:meeting|call)\b',
        r'^.{0,40}\b(?:hat|ist) (?:die besprechung|das meeting|dem meeting|der besprechung) (?:beigetreten|verlassen)\b',
        r'^\S+ (?:joined|left)(?: the (?:meeting|call))?\.?$',
        r'^(?:please )?(?:be aware|note) that (?:this|the) (?:meeting|call|session) (?:is|will be) recorded\b',
        r'^(?:by continuing|by staying in this meeting),? you (?:consent|agree)\b',
        r'^(?:transcript|transcription) (?:provided|generated) by\b',
        r'^(?:live )?(?:captions?|untertitel) (?:are |is |sind |ist )?(?:now )?(?:on|off|enabled|disabled|aktiviert|deaktiviert|an|aus)\b',
    )
)
_TURN_GAP_SECONDS = 8.0


@dataclass
class _Cue:
    start: float
    end: float
    speaker: str | None
    text: str


@dataclass
class _Turn:
    start: float
    end: float
    speaker: str | None
    lines: list[str]


def _parse_timestamp(value: str) -> float | None:
    match = _TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f'{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}'


def _clean_cue_text(raw: str) -> tuple[str | None, str]:
    """Return (voice-tag speaker or None, text) for one cue: every tag is
    dropped, entities decoded, whitespace collapsed and non-speech
    annotations removed."""
    speaker: str | None = None
    voice = _VOICE_TAG_RE.search(raw)
    if voice:
        speaker = ' '.join(voice.group(1).split()) or None
    text = _ANY_TAG_RE.sub('', raw)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = _BRACKET_ANNOTATION_RE.sub(' ', text)
    text = _PAREN_ANNOTATION_RE.sub(' ', text)
    return speaker, ' '.join(text.split())


def _promote_inline_speakers(cues: list[_Cue]) -> None:
    """Zoom writes `Name: text` instead of voice tags. When no cue carries a
    voice tag, a prefix that recurs in at least _INLINE_SPEAKER_MIN_CUES
    cues is treated as the speaker and stripped from the text."""
    if any(cue.speaker for cue in cues):
        return
    counts: dict[str, int] = {}
    for cue in cues:
        match = _INLINE_SPEAKER_RE.match(cue.text)
        if match:
            name = match.group(1).strip()
            counts[name] = counts.get(name, 0) + 1
    speakers = {name for name, count in counts.items() if count >= _INLINE_SPEAKER_MIN_CUES}
    if not speakers:
        return
    for cue in cues:
        match = _INLINE_SPEAKER_RE.match(cue.text)
        if match and match.group(1).strip() in speakers:
            cue.speaker = match.group(1).strip()
            cue.text = cue.text[match.end():]


def _is_boilerplate(text: str) -> bool:
    return any(pattern.search(text) for pattern in _BOILERPLATE_PATTERNS)


def _split_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line.rstrip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def parse_vtt(text: str) -> list[_Cue]:
    """Parse WebVTT (and, leniently, SRT-style `,` millisecond separators)
    into cleaned cues, dropping header/NOTE/STYLE/REGION blocks, cues
    without a parsable timing line, empty cues and boilerplate cues."""
    cues: list[_Cue] = []
    for block in _split_blocks(text.lstrip('﻿')):
        first = block[0].strip()
        if first.startswith('WEBVTT') or first.split(' ', 1)[0] in {'NOTE', 'STYLE', 'REGION'}:
            continue
        timing_index = next((index for index, line in enumerate(block[:2]) if _TIMING_LINE_RE.match(line)), None)
        if timing_index is None:
            continue
        timing = _TIMING_LINE_RE.match(block[timing_index])
        assert timing is not None
        start = _parse_timestamp(timing.group(1))
        end = _parse_timestamp(timing.group(2))
        if start is None or end is None:
            continue
        speaker, cue_text = _clean_cue_text(' '.join(block[timing_index + 1:]))
        if not cue_text or _is_boilerplate(cue_text):
            continue
        cues.append(_Cue(start=start, end=end, speaker=speaker, text=cue_text))
    _promote_inline_speakers(cues)
    return [cue for cue in cues if cue.text and not _is_boilerplate(cue.text)]


def _append_line(lines: list[str], text: str) -> None:
    """Add a cue's text to a turn, absorbing roll-up repetition: an exact
    repeat of the previous line is skipped, a line that extends the
    previous one replaces it, a line the previous one already contains is
    skipped."""
    if not lines:
        lines.append(text)
        return
    previous = lines[-1]
    if text == previous or text in previous:
        return
    if text.startswith(previous):
        lines[-1] = text
        return
    lines.append(text)


def _merge_turns(cues: list[_Cue]) -> list[_Turn]:
    turns: list[_Turn] = []
    for cue in cues:
        current = turns[-1] if turns else None
        if current is not None and current.speaker == cue.speaker and cue.start - current.end <= _TURN_GAP_SECONDS:
            _append_line(current.lines, cue.text)
            current.end = max(current.end, cue.end)
        else:
            turns.append(_Turn(start=cue.start, end=cue.end, speaker=cue.speaker, lines=[cue.text]))
    return turns


def vtt_to_markdown(text: str, title: str | None = None) -> str:
    """Body markdown for a WebVTT transcript (see module docstring)."""
    cues = parse_vtt(text)
    turns = _merge_turns(cues)
    lines: list[str] = []
    if title:
        lines.append(f'# {title}')
        lines.append('')
    if not turns:
        lines.append('*Das Transkript enthält keine verwertbaren Gesprächsbeiträge.*')
        return '\n'.join(lines) + '\n'

    speakers: list[str] = []
    for turn in turns:
        if turn.speaker and turn.speaker not in speakers:
            speakers.append(turn.speaker)
    duration = max(turn.end for turn in turns)
    if speakers:
        lines.append(f"**Teilnehmende:** {', '.join(speakers)}  ")
    lines.append(f'**Dauer:** {_format_timestamp(duration)}')
    lines.append('')
    lines.append('## Transkript')
    lines.append('')
    for turn in turns:
        body = ' '.join(turn.lines)
        stamp = f'[{_format_timestamp(turn.start)}]'
        lines.append(f'**{turn.speaker}** {stamp}: {body}' if turn.speaker else f'{stamp} {body}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'
