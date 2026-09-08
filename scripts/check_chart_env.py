#!/usr/bin/env python3
"""Gleicht die Umgebung im gerenderten Helm-Chart gegen weave.yaml ab.

Warum es dieses Skript gibt
---------------------------
weave.yaml ist die einzige Konfigurationsquelle der Plattform. Der
Compose-Stack liest sie ueber `weave_config.py render` (deploy/.env), das
Chart ueber `weave_config.py helm-values` (values.generated.yaml). Das
schuetzt die WERTE vor dem Auseinanderlaufen, aber nicht die ZUORDNUNG: ob
ein Dienst im Cluster wirklich jede Variable sieht, die weave.yaml fuer ihn
deklariert, entscheiden die Workload-Templates -- ihre `only`-, `except`-
und `rename`-Parameter. Eine dort vergessene Variable faellt beim Rendern
nicht auf. Sie faellt auf, wenn der Container startet und seine eigene
Pflichtwertpruefung ihn abbricht, oder -- schlimmer -- wenn er startet und
still auf einen Vorgabewert zurueckfaellt.

Also: `helm template` rendern, die Deployments parsen, und je Workload die
MENGE der Umgebungsvariablen mit der vergleichen, die weave.yaml fuer
seinen Dienst deklariert. In BEIDE Richtungen.

Erwartete Abweichungen
----------------------
Nicht jede Abweichung ist ein Fehler, aber jede braucht einen Grund. Die
Gruende stehen unten in DEVIATIONS, eine Zeile je Variable und Workload.
Genau das ist der Zweck: eine neue Abweichung faellt auf, weil sie in
dieser Tabelle fehlt, und wer sie eintraegt, muss sie benennen.

Drei Quellen legitimer Abweichungen:

  - .Values.env.exclude aus der values.yaml des Charts. Variablen, die im
    Cluster keine Bedeutung haben (Image-Tags, Host-Ports von Compose,
    Compose-Container-Limits). Diese Liste wird GELESEN, nicht wiederholt.
  - Umbenennungen. weave.yaml benennt eine Variable so, wie der Schluessel
    in der .env heisst; der Container liest sie teils unter einem anderen
    Namen (ADR-0003). Steht in RENAMES, abgeglichen gegen
    deploy/docker-compose.weave.yml.
  - Vom Chart bestimmte Werte. DATABASE_URL, REDIS_URL, POSTGRES_HOST und
    die internen Basis-URLs beschreiben in weave.yaml die Adressierung des
    Compose-Stacks (Docker-Servicenamen), die es im Cluster nicht gibt.
    Steht in DEVIATIONS als "chart".

Aufruf
------
    services/tools/.venv/bin/python scripts/check_chart_env.py

Exit 0, wenn jede Abweichung erklaert ist; sonst 1 und eine Liste.
Braucht helm im PATH und PyYAML.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from weave_config import load_items  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "weave.yaml"
CHART_PATH = REPO_ROOT / "deploy" / "charts" / "weave"
CHART_VALUES = CHART_PATH / "values.yaml"
GENERATED_VALUES = CHART_PATH / "values.generated.yaml"


# ---------------------------------------------------------------------------
# Welcher Container zu welchem Dienst in weave.yaml gehoert
# ---------------------------------------------------------------------------

# Der Containername im gerenderten Manifest -> die weave.yaml-Dienste, aus
# denen dieser Container seine Umgebung bezieht. ingest-frontend zieht aus
# zweien: WEAVE_CHAT_PUBLIC_URL steht in weave.yaml beim Dienst chat,
# gelesen wird sie aber vom Ingest-Frontend (so auch in der Compose-Datei).
CONTAINERS: dict[str, tuple[str, ...]] = {
    "ingest-backend": ("ingest",),
    "ingest-worker": ("ingest",),
    "ingest-frontend": ("ingest", "chat"),
    "knowledge": ("knowledge",),
    "knowledge-worker": ("knowledge",),
    "retrieval": ("retrieval",),
    "runtime": ("runtime",),
    "api": ("api",),
    "tools-backend": ("tools",),
    "tools-mcp": ("tools",),
    "chat": ("chat",),
    "embeddings": ("embeddings",),
    "reranker": ("reranker",),
    "postgres": ("postgres",),
    "redis": ("redis",),
    # Kein Anwendungscontainer: der db-bootstrap-Job hat seine eigene,
    # rein prozedurale Umgebung (PGHOST, OWNER, DB_*, WAIT_*). Er wird
    # weiter unten nur auf RETRIEVAL_DB_PASSWORD geprueft.
}

# Umbenennungen beim Durchreichen, abgeglichen gegen
# deploy/docker-compose.weave.yml: {container: {weave.yaml-Name: Containername}}.
RENAMES: dict[str, dict[str, str]] = {
    "knowledge": {
        "WEAVE_KNOWLEDGE_SECRET_KEY": "SECRET_KEY",
        "WEAVE_KNOWLEDGE_INGEST_API_TOKEN": "WEAVE_INGEST_API_TOKEN",
        "WEAVE_KNOWLEDGE_WEBHOOK_SECRET": "WEAVE_INGEST_WEBHOOK_SECRET",
    },
    "api": {"WEAVE_API_SECRET_KEY": "SECRET_KEY"},
    "chat": {"CHAT_APP_BASE_URL": "APP_BASE_URL"},
}
RENAMES["knowledge-worker"] = RENAMES["knowledge"]


@dataclass(frozen=True)
class Deviation:
    """Eine erlaubte Abweichung samt Begruendung.

    kind "chart":   im Manifest, nicht in weave.yaml -- das Chart bestimmt
                    den Wert (Cluster-Adressierung, Deployment-Schalter).
    kind "dropped": in weave.yaml, absichtlich nicht im Container.
    """

    kind: str
    reason: str


# Gilt fuer JEDEN Container. Der Schluessel ist der Name im Manifest.
GLOBAL: dict[str, Deviation] = {}

# Je Container. Der Schluessel ist der Name im Manifest ("chart") bzw. der
# Name in weave.yaml ("dropped").
DEVIATIONS: dict[str, dict[str, Deviation]] = {
    "ingest-backend": {
        "POSTGRES_HOST": Deviation(
            "chart",
            "In weave.yaml gar nicht deklariert -- die Compose-Datei traegt "
            "dort den Docker-Servicenamen 'postgres' fest ein. Im Cluster "
            "haengt der Host am postgresql.mode (weave.postgresHost).",
        ),
        "REDIS_URL": Deviation(
            "chart",
            "Compose baut sie inline aus Servicenamen und Passwort. Im "
            "Cluster: weave.redisUrl, logische DB 0 (ADR-0001), mit "
            "$(REDIS_PASSWORD) statt eingesetztem Passwort.",
        ),
        "RUN_ALEMBIC_ON_STARTUP": Deviation(
            "chart",
            "Ob dieses Deployment beim Start migriert, ist eine Eigenschaft "
            "des Deployments, nicht der Plattform. Kein weave.yaml-Wert.",
        ),
    },
    "ingest-worker": {
        "POSTGRES_HOST": Deviation("chart", "wie ingest-backend"),
        "REDIS_URL": Deviation("chart", "wie ingest-backend"),
        "HOME": Deviation(
            "chart",
            "PaddleOCR laedt seine Gewichte nach $HOME. Der Modell-Cache "
            "haengt daran (ingestWorker.modelCache), nicht an weave.yaml.",
        ),
    },
    "ingest-frontend": {
        # Das Frontend bekommt aus dem gemeinsamen ingest-Block nur die
        # beiden URLs, die es wirklich liest -- so auch in der Compose-Datei.
        # Alles andere waere ein Datenbankpasswort in einem Browser-Frontend.
        var: Deviation(
            "dropped",
            "Ingest-Backend, -Worker und -Frontend teilen einen "
            "weave.yaml-Dienst. Das Frontend liest laut Compose-Datei nur "
            "WEAVE_INGEST_PUBLIC_API_URL und WEAVE_CHAT_PUBLIC_URL.",
        )
        for var in (
            "BOOTSTRAP_ADMIN_USERNAME",
            "BOOTSTRAP_ADMIN_EMAIL",
            "BOOTSTRAP_ADMIN_PASSWORD",
            "KNOWLEDGE_INGEST_API_TOKEN",
            "CELERY_MAX_TASKS_PER_CHILD",
            "CELERY_PREFETCH_MULTIPLIER",
            "CELERY_WORKER_CONCURRENCY",
            "CHAT_CONFIG_SERVICE_TOKEN",
            "CHAT_LLM_PRIVATE_HOST_ALLOWLIST",
            "CORS_ORIGINS",
            "HANDOFF_CALLBACK_URL",
            "HANDOFF_SECRET",
            "RUNTIME_API_TOKEN",
            "RUNTIME_BOTS_BASE_URL",
            "PORTAL_KNOWLEDGE_BASE_URL",
            "PORTAL_KNOWLEDGE_WEBHOOK_SECRET",
            "POSTGRES_DB",
            "POSTGRES_PASSWORD",
            "POSTGRES_PORT",
            "POSTGRES_USER",
            "PUBLIC_API_URL",
            "REDIS_PASSWORD",
            "SECRET_KEY",
            "WEBHOOK_PRIVATE_HOST_ALLOWLIST",
            # Aus dem chat-Block: die liest die Chat-Oberflaeche selbst.
            "CHAT_APP_BASE_URL",
            "WEAVE_API_INGEST_LOGIN_ENABLED",
            "WEAVE_API_OIDC_ENABLED",
            "WEAVE_API_PUBLIC_BASE_URL",
        )
    },
    "knowledge": {
        "DATABASE_URL": Deviation(
            "chart",
            "Compose setzt sie inline aus Servicename und Passwort "
            "zusammen. Im Cluster: weave.databaseUrl gegen "
            "postgresql.databases.knowledge, mit $(POSTGRES_PASSWORD).",
        ),
        "REDIS_URL": Deviation(
            "chart", "weave.redisUrl, logische DB 1 -- nicht 0 (ADR-0001)."
        ),
        "WEAVE_INGEST_BASE_URL": Deviation(
            "chart",
            "Compose traegt http://weave-ingest-backend:8000 fest ein; "
            "weave.yaml fuehrt sie deshalb bewusst gar nicht (siehe dessen "
            "Kopfkommentar). Im Cluster: weave.internalUrl.",
        ),
        "RUN_ALEMBIC_ON_STARTUP": Deviation(
            "chart", "wie ingest-backend: Deployment-Schalter, kein Plattformwert."
        ),
        "POSTGRES_USER": Deviation(
            "dropped",
            "Geht nur in die DATABASE_URL ein; der Container liest sie nicht "
            "einzeln. Die Compose-Datei reicht sie hier ebenfalls nicht durch.",
        ),
        "POSTGRES_PORT": Deviation("dropped", "wie POSTGRES_USER"),
    },
    "knowledge-worker": {
        "DATABASE_URL": Deviation("chart", "wie knowledge"),
        "REDIS_URL": Deviation("chart", "wie knowledge"),
        "WEAVE_INGEST_BASE_URL": Deviation("chart", "wie knowledge"),
        "POSTGRES_USER": Deviation("dropped", "wie knowledge"),
        "POSTGRES_PORT": Deviation("dropped", "wie knowledge"),
    },
    "retrieval": {
        "DATABASE_URL": Deviation(
            "chart",
            "weave.databaseUrl gegen postgresql.databases.knowledge, aber "
            "als RETRIEVAL_DB_USER mit $(RETRIEVAL_DB_PASSWORD): Retrieval "
            "liest nur (ADR-0005).",
        ),
        "RETRIEVAL_DB_USER": Deviation(
            "dropped",
            "Geht nur in die DATABASE_URL ein. Die Compose-Datei reicht sie "
            "ebenfalls nicht durch.",
        ),
    },
    "runtime": {
        "RETRIEVAL_BASE_URL": Deviation(
            "chart",
            "Compose traegt http://weave-retrieval:8000 fest ein, weave.yaml "
            "fuehrt sie nicht. Im Cluster: weave.internalUrl.",
        ),
    },
    "api": {
        "DATABASE_URL": Deviation(
            "chart",
            "weave.databaseUrl gegen postgresql.databases.api, mit "
            "$(POSTGRES_PASSWORD).",
        ),
        "RUNTIME_BASE_URL": Deviation("chart", "Compose fest verdrahtet, weave.yaml fuehrt sie nicht."),
        "RETRIEVAL_BASE_URL": Deviation("chart", "wie RUNTIME_BASE_URL"),
        "RUN_ALEMBIC_ON_STARTUP": Deviation("chart", "Deployment-Schalter (ADR-0004)."),
        "POSTGRES_USER": Deviation("dropped", "geht nur in die DATABASE_URL ein"),
        "POSTGRES_PORT": Deviation("dropped", "geht nur in die DATABASE_URL ein"),
    },
    "tools-backend": {
        "WEAVE_API_BASE_URL": Deviation("chart", "Compose fest verdrahtet, weave.yaml fuehrt sie nicht."),
        "RETRIEVAL_BASE_URL": Deviation("chart", "wie WEAVE_API_BASE_URL"),
    },
    "tools-mcp": {
        "WEAVE_API_BASE_URL": Deviation("chart", "wie tools-backend"),
        "RETRIEVAL_BASE_URL": Deviation("chart", "wie tools-backend"),
        "TOOLS_API_TOKEN": Deviation(
            "dropped",
            "Die Tuer VOR der REST-Spiegelung. Gelesen wird sie allein in "
            "services/tools/app/api/deps.py (Header X-Tools-Service-Token); "
            "services/tools/app/core/config.py gibt ihr den Vorgabewert '', "
            "der Import scheitert ohne sie also nicht. Die MCP-Oberflaeche "
            "hat diese Tuer nicht -- sie loest jeden Aufruf ueber den "
            "Authorization-Header des Aufrufers auf. Ein Geheimnis, das eine "
            "Anwendung nicht liest, gehoert nicht in ihre Umgebung.",
        ),
    },
    "chat": {
        "WEAVE_API_BASE_URL": Deviation("chart", "Compose fest verdrahtet, weave.yaml fuehrt sie nicht."),
        "WEAVE_CHAT_PUBLIC_URL": Deviation(
            "dropped",
            "Steht in weave.yaml beim Dienst chat, gelesen wird sie aber vom "
            "Ingest-Frontend (Link zur Chat-Oberflaeche). Genau so in der "
            "Compose-Datei.",
        ),
    },
    "postgres": {
        "PGDATA": Deviation(
            "chart",
            "Unterverzeichnis im Mountpunkt. Ohne das verweigert initdb bei "
            "Volumes, die ein lost+found mitbringen. Kein Plattformwert.",
        ),
        "RETRIEVAL_DB_PASSWORD": Deviation(
            "dropped",
            "Im Compose-Stack liest deploy/postgres-init/01-create-databases.sh "
            "sie IM postgres-Container. Im Cluster legt der db-bootstrap-Job "
            "die Rolle an -- in allen drei postgresql.mode. Das Passwort "
            "gehoert deshalb dorthin und nicht hierher; geprueft wird das "
            "unten eigens.",
        ),
    },
}


# ---------------------------------------------------------------------------
# Chart rendern und parsen
# ---------------------------------------------------------------------------

# Alles eingeschaltet, damit jeder Workload wirklich im Manifest auftaucht.
# postgresql.mode bleibt bundled: nur dieser Modus rendert einen
# postgres-Container, und die Umgebung der Anwendungscontainer haengt nicht
# am Modus (nur der Host in der DATABASE_URL, den dieses Skript nicht prueft).
HELM_SET = [
    "secrets.existingSecret=weave-platform-secrets",
    "embeddings.enabled=true",
    "reranker.enabled=true",
    "toolsBackend.mcp.enabled=true",
]


def render(chart: Path) -> list[dict]:
    cmd = ["helm", "template", "weave", str(chart), "-f", str(GENERATED_VALUES)]
    for s in HELM_SET:
        cmd += ["--set", s]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"helm template ist fehlgeschlagen:\n{proc.stderr}")
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def container_env(docs: list[dict]) -> dict[str, set[str]]:
    """{Containername: Menge der env-Namen} ueber alle Deployments und Jobs."""
    found: dict[str, set[str]] = {}
    for doc in docs:
        if doc.get("kind") not in ("Deployment", "Job"):
            continue
        spec = doc["spec"]["template"]["spec"]
        for c in spec.get("initContainers", []) + spec["containers"]:
            names = {e["name"] for e in c.get("env", [])}
            if c["name"] in found:
                raise SystemExit(
                    f"Zwei Container heissen {c['name']} -- die Zuordnung zu "
                    f"einem weave.yaml-Dienst waere mehrdeutig."
                )
            found[c["name"]] = names
    return found


def declared(config_path: Path) -> dict[str, set[str]]:
    """{weave.yaml-Dienst: Menge der fuer ihn deklarierten Variablennamen}."""
    out: dict[str, set[str]] = {}
    for item in load_items(config_path):
        for service, var in item.targets:
            out.setdefault(service, set()).add(var)
    return out


def excluded(values_path: Path) -> set[str]:
    """.Values.env.exclude -- gelesen, nicht wiederholt."""
    values = yaml.safe_load(values_path.read_text(encoding="utf-8"))
    return set((values.get("env") or {}).get("exclude") or [])


# ---------------------------------------------------------------------------
# Abgleich
# ---------------------------------------------------------------------------


@dataclass
class Report:
    findings: list[str] = field(default_factory=list)
    checked: int = 0

    def fail(self, msg: str) -> None:
        self.findings.append(msg)


def compare(report: Report) -> None:
    docs = render(CHART_PATH)
    actual = container_env(docs)
    decl = declared(CONFIG_PATH)
    skip = excluded(CHART_VALUES)

    for container, services in sorted(CONTAINERS.items()):
        if container not in actual:
            report.fail(
                f"{container}: kein Container dieses Namens im gerenderten "
                f"Manifest. Entweder ist der Workload nicht eingeschaltet "
                f"(HELM_SET) oder sein Template fehlt."
            )
            continue

        for service in services:
            if service not in decl:
                report.fail(
                    f"{container}: weave.yaml kennt keinen Dienst {service!r}."
                )

        expected: set[str] = set()
        for service in services:
            expected |= decl.get(service, set())
        expected -= skip

        renames = RENAMES.get(container, {})
        expected = {renames.get(v, v) for v in expected}

        deviations = {**GLOBAL, **DEVIATIONS.get(container, {})}
        # Eine "dropped"-Ausnahme kann auf den weave.yaml-Namen lauten; nach
        # der Umbenennung wird gegen den Containernamen verglichen.
        dropped = {
            renames.get(k, k)
            for k, d in deviations.items()
            if d.kind == "dropped"
        }
        chart_owned = {k for k, d in deviations.items() if d.kind == "chart"}

        got = actual[container]
        missing = expected - got - dropped
        extra = got - expected - chart_owned

        report.checked += 1
        for var in sorted(missing):
            report.fail(
                f"{container}: {var} steht in weave.yaml (Dienst "
                f"{'/'.join(services)}), fehlt aber im gerenderten Container. "
                f"Der Dienst faellt damit still auf seinen Vorgabewert zurueck "
                f"oder bricht beim Start ab."
            )
        for var in sorted(extra):
            report.fail(
                f"{container}: {var} steht im gerenderten Container, aber in "
                f"keinem weave.yaml-Dienst dieses Workloads. Entweder gehoert "
                f"sie nach weave.yaml, oder sie braucht einen Eintrag in "
                f"DEVIATIONS mit Begruendung."
            )

        # Eine als "dropped" eingetragene Ausnahme, die tatsaechlich da ist,
        # ist genauso ein Befund: die Tabelle beschreibt das Chart dann nicht
        # mehr.
        for var in sorted(dropped & got):
            report.fail(
                f"{container}: {var} ist in DEVIATIONS als bewusst "
                f"weggelassen eingetragen, steht aber im Manifest."
            )
        for var in sorted(chart_owned - got):
            report.fail(
                f"{container}: {var} ist in DEVIATIONS als vom Chart gesetzt "
                f"eingetragen, steht aber nicht im Manifest."
            )

    stray = set(actual) - set(CONTAINERS) - {"db-bootstrap"}
    for container in sorted(stray):
        report.fail(
            f"{container}: Container ohne Zuordnung zu einem "
            f"weave.yaml-Dienst. CONTAINERS in diesem Skript ergaenzen."
        )

    # Der db-bootstrap-Job ist kein Anwendungscontainer, aber er ist der
    # einzige Ort, an dem RETRIEVAL_DB_PASSWORD aus weave.yaml im Cluster
    # ankommt (siehe die Begruendung bei postgres). Faellt das weg,
    # bekommt weave_retrieval_ro ein leeres Passwort.
    if "db-bootstrap" in actual:
        if "RETRIEVAL_DB_PASSWORD" not in actual["db-bootstrap"]:
            report.fail(
                "db-bootstrap: RETRIEVAL_DB_PASSWORD fehlt. Dann legt der Job "
                "die Read-only-Rolle ohne Passwort an und Weave-Retrieval "
                "kommt nicht herein."
            )
        report.checked += 1
    else:
        report.fail("db-bootstrap: der Job wurde nicht gerendert.")


def main() -> int:
    if not GENERATED_VALUES.exists():
        print(
            f"{GENERATED_VALUES} fehlt. Erzeugen mit\n"
            f"    python scripts/weave_config.py helm-values",
            file=sys.stderr,
        )
        return 2

    report = Report()
    compare(report)

    if report.findings:
        print(f"{len(report.findings)} Abweichung(en):\n", file=sys.stderr)
        for f in report.findings:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(
        f"{report.checked} Container geprueft: jede Umgebungsvariable im "
        f"gerenderten Chart ist entweder in weave.yaml deklariert oder in "
        f"diesem Skript begruendet, und umgekehrt."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
