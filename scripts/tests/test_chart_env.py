"""Der Abgleich Chart <-> weave.yaml als Test.

Die Arbeit steckt in scripts/check_chart_env.py; hier wird sie nur
angestossen, damit sie in derselben Strecke laeuft wie die uebrigen Tests.

Uebersprungen, wenn helm nicht im PATH ist oder values.generated.yaml
fehlt -- beides sind Voraussetzungen des Skripts, keine Aussagen ueber das
Chart. Ein Fehlschlag ist dagegen immer eine echte Abweichung.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHECK = REPO_ROOT / "scripts" / "check_chart_env.py"
GENERATED = REPO_ROOT / "deploy" / "charts" / "weave" / "values.generated.yaml"


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm nicht im PATH")
@pytest.mark.skipif(
    not GENERATED.exists(),
    reason="values.generated.yaml fehlt (weave_config.py helm-values)",
)
def test_chart_env_matches_weave_yaml() -> None:
    proc = subprocess.run(
        [sys.executable, str(CHECK)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm nicht im PATH")
def test_helm_values_file_is_current() -> None:
    """values.generated.yaml muss zu weave.yaml passen.

    Sonst prueft der Abgleich oben eine veraltete Datei und meldet nichts,
    obwohl eine in weave.yaml neu deklarierte Variable in keinem Container
    ankommt.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "values.generated.yaml"
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "weave_config.py"),
                "helm-values",
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert out.read_text(encoding="utf-8") == GENERATED.read_text(
            encoding="utf-8"
        ), (
            "deploy/charts/weave/values.generated.yaml ist nicht mehr das, was "
            "weave.yaml ergibt. Neu erzeugen:\n"
            "    python scripts/weave_config.py helm-values"
        )
