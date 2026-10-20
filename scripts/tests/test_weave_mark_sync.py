"""Der Abgleich der Weave-Zeichen-Kopien mit packages/weave-mark als Test.

Die Arbeit steckt in scripts/check_weave_mark.py; hier wird sie nur
angestossen, damit sie in derselben Strecke laeuft wie die uebrigen
Plattform-Tests. Ein Fehlschlag ist immer eine echte Abweichung: eine Kopie
des Zeichens oder einer Bewegung wurde geaendert, ohne die kanonische
Fassung (oder die anderen Kopien) nachzuziehen.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHECK = REPO_ROOT / "scripts" / "check_weave_mark.py"


def _load_check():
    spec = importlib.util.spec_from_file_location("check_weave_mark", CHECK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_mark_copies_match_canonical() -> None:
    problems = _load_check().check()
    assert problems == [], "\n".join(problems)


def test_normalisation_tolerates_formatting_but_not_values() -> None:
    check = _load_check()
    one_line = "@keyframes weave-turn { 0%, 10% { transform: rotate(0); } 90%, 100% { transform: rotate(180deg); } }"
    multi_line = "@keyframes weave-turn {\n  0%, 10% { transform: rotate(0); }\n  90%, 100% { transform: rotate(180deg); }\n}"
    assert check.keyframes(one_line) == check.keyframes(multi_line)
    assert check.keyframes("@keyframes weave-x { 0% { transform: translateX(0.5px); } }") == check.keyframes(
        "@keyframes weave-x { 0% { transform: translateX(.5px); } }"
    )
    changed = one_line.replace("180deg", "90deg")
    assert check.keyframes(one_line) != check.keyframes(changed)

    jsx = '<svg><rect x="1" strokeWidth="1" className="weave-knots" /></svg>'
    svg = '<svg><rect class="weave-knots" stroke-width="1" x="1"/></svg>'
    assert check.mark_geometry(jsx) == check.mark_geometry(svg)
