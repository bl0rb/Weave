#!/usr/bin/env python3
"""Abgleich der Kopien des Weave-Zeichens mit packages/weave-mark.

Das Zeichen (Inline-SVG) und die Keyframes seiner Bewegungen liegen in drei
getrennt gebauten Oberflächen als Kopie vor (siehe packages/weave-mark/
README.md). Dieses Skript vergleicht Geometrie und Keyframes jeder Kopie mit
der kanonischen Fassung und meldet jede Abweichung mit Datei und Fundstelle.
Exit-Code 1 bei Abweichungen; scripts/tests/test_weave_mark_sync.py lässt es
im CI mitlaufen.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SVG = REPO_ROOT / "packages" / "weave-mark" / "weave-mark.svg"
CANONICAL_CSS = REPO_ROOT / "packages" / "weave-mark" / "motion.css"

MARK_COPIES = [
    REPO_ROOT / "services" / "chat" / "src" / "components" / "weave-logo.tsx",
    REPO_ROOT / "services" / "ingest" / "frontend" / "src" / "components" / "weave-ingest-logo.tsx",
    REPO_ROOT / "clients" / "desktop" / "ui" / "common.js",
]
CSS_COPIES = [
    REPO_ROOT / "services" / "chat" / "src" / "app" / "globals.css",
    REPO_ROOT / "services" / "ingest" / "frontend" / "src" / "app" / "globals.css",
    REPO_ROOT / "clients" / "desktop" / "ui" / "style.css",
]

_TAG_RE = re.compile(r"<(rect|path|g)\b([^>]*?)/?>")
_ATTR_RE = re.compile(r"([A-Za-z-]+)=\"([^\"]*)\"")
_JSX_ATTR_NAMES = {"className": "class", "strokeWidth": "stroke-width"}


def mark_geometry(text: str) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """Every rect/path/g inside the (first) <svg …>…</svg> of `text`, as
    (tag, sorted attributes) -- JSX attribute names mapped to SVG ones. The
    outer <svg> itself is left out: its classes differ per host on purpose."""
    # The desktop copy keeps the mark's body in a template literal
    # (`WEAVE_MARK = \`…\``) and wraps it in <svg> at run time.
    literal = re.search(r"WEAVE_MARK\s*=\s*`([^`]*)`", text)
    if literal:
        body = literal.group(1)
    else:
        start = text.find("<svg")
        end = text.find("</svg>", start)
        if start < 0 or end < 0:
            raise ValueError("kein <svg>…</svg> gefunden")
        body = text[start:end]
    elements = []
    for tag, raw_attrs in _TAG_RE.findall(body):
        attrs = tuple(sorted((_JSX_ATTR_NAMES.get(name, name), value) for name, value in _ATTR_RE.findall(raw_attrs)))
        elements.append((tag, attrs))
    return elements


def keyframes(text: str) -> dict[str, str]:
    """`@keyframes weave-*` blocks by name, normalised: whitespace removed,
    `0.65` -> `.65`, lower case -- so a one-line and a multi-line copy compare
    equal while any changed value shows up."""
    found: dict[str, str] = {}
    for match in re.finditer(r"@keyframes\s+(weave-[a-z]+)\s*\{", text):
        depth, i = 1, match.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        block = text[match.end():i - 1]
        block = re.sub(r"\s+", "", block).lower()
        block = re.sub(r"(?<![\d.])0\.(\d)", r".\1", block)
        found[match.group(1)] = block
    return found


def check() -> list[str]:
    problems: list[str] = []
    canonical_geometry = mark_geometry(CANONICAL_SVG.read_text(encoding="utf-8"))
    for copy in MARK_COPIES:
        try:
            geometry = mark_geometry(copy.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{copy.relative_to(REPO_ROOT)}: {exc}")
            continue
        if geometry != canonical_geometry:
            first = next((i for i, (a, b) in enumerate(zip(geometry, canonical_geometry)) if a != b), min(len(geometry), len(canonical_geometry)))
            problems.append(
                f"{copy.relative_to(REPO_ROOT)}: Zeichen weicht ab Element {first + 1} "
                f"({len(geometry)} statt {len(canonical_geometry)} Elemente) von packages/weave-mark/weave-mark.svg"
            )

    canonical_frames = keyframes(CANONICAL_CSS.read_text(encoding="utf-8"))
    for copy in CSS_COPIES:
        frames = keyframes(copy.read_text(encoding="utf-8"))
        for name, block in canonical_frames.items():
            if name not in frames:
                problems.append(f"{copy.relative_to(REPO_ROOT)}: @keyframes {name} fehlt")
            elif frames[name] != block:
                problems.append(f"{copy.relative_to(REPO_ROOT)}: @keyframes {name} weicht von packages/weave-mark/motion.css ab")
        for name in frames:
            if name not in canonical_frames:
                problems.append(f"{copy.relative_to(REPO_ROOT)}: @keyframes {name} ist nicht in packages/weave-mark/motion.css")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    if not problems:
        print(f"weave-mark: {len(MARK_COPIES)} Zeichen-Kopien und {len(CSS_COPIES)} Keyframe-Kopien stimmen mit packages/weave-mark überein")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
