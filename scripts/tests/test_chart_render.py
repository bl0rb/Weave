"""Render-Tests fuer die Skalierungsteile des Helm-Charts: kein gemeinsames
Volume, zwei Worker-Pools, KEDA-Schalter und Verbindungsbudget.

Uebersprungen, wenn helm nicht im PATH ist.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHART = REPO_ROOT / "deploy" / "charts" / "weave"
KEDA_API = "keda.sh/v1alpha1/ScaledObject"
ALL_AUTOSCALING = [
    f"autoscaling.{key}.enabled=true"
    for key in (
        "ingestBackend", "ingestWorker", "ingestWorkerIo", "knowledge",
        "knowledgeWorker", "retrieval", "api", "toolsMcp",
    )
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm nicht im PATH")


def _render(*sets: str, api_versions: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    cmd = ["helm", "template", "t", str(CHART), "--set", "secrets.existingSecret=s"]
    for value in sets:
        cmd += ["--set", value]
    for api in api_versions:
        cmd += ["--api-versions", api]
    return subprocess.run(cmd, capture_output=True, text=True)


def _docs(*sets: str, api_versions: tuple[str, ...] = ()) -> list[dict]:
    proc = _render(*sets, api_versions=api_versions)
    assert proc.returncode == 0, proc.stderr
    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def _kinds(docs: list[dict], kind: str) -> list[str]:
    return [doc["metadata"]["name"] for doc in docs if doc["kind"] == kind]


def _env(deployment: dict) -> dict[str, str]:
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in container.get("env", [])}


def test_no_shared_storage_and_two_worker_pools() -> None:
    docs = _docs()
    assert "t-weave-ingest-storage" not in _kinds(docs, "PersistentVolumeClaim")
    deployments = {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "Deployment"}
    for name, doc in deployments.items():
        for volume in doc["spec"]["template"]["spec"].get("volumes", []):
            if "persistentVolumeClaim" in volume:
                # Nur zustandsbehaftete Dienste und Modell-Caches.
                assert name.endswith(("-postgres", "-redis", "-embeddings", "-reranker")) or volume["name"] == "model-cache", (name, volume)

    ocr = _env(deployments["t-weave-ingest-worker"])
    io = _env(deployments["t-weave-ingest-worker-io"])
    assert ocr["CELERY_QUEUES"] == "weave.ingest.ocr"
    assert io["CELERY_QUEUES"] == "weave.ingest.default"
    assert io["CELERY_WORKER_CONCURRENCY"] == "4"
    assert ocr["WORKER_TMP_DIR"] == io["WORKER_TMP_DIR"] == "/scratch/tasks"
    assert deployments["t-weave-ingest-worker"]["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 120


def test_default_autoscaling_maxima_fit_the_connection_budget() -> None:
    docs = _docs(*ALL_AUTOSCALING)
    assert "t-weave-tools-mcp" in _kinds(docs, "HorizontalPodAutoscaler")


def test_connection_budget_overrun_fails_with_a_breakdown() -> None:
    proc = _render(*ALL_AUTOSCALING, "autoscaling.ingestWorker.maxReplicas=30")
    assert proc.returncode != 0
    assert "Verbindungsbudget ueberschritten" in proc.stderr
    assert "ingestWorker: 30 Repliken x 4 = 120" in proc.stderr


def test_keda_needs_the_operator_api() -> None:
    proc = _render("autoscaling.ingestWorker.enabled=true", "autoscaling.ingestWorker.engine=keda")
    assert proc.returncode != 0
    assert "keda.sh/v1alpha1 fehlt" in proc.stderr


def test_keda_replaces_the_ocr_hpa_and_keeps_passwords_out_of_the_manifest() -> None:
    docs = _docs(
        "autoscaling.ingestWorker.enabled=true", "autoscaling.ingestWorker.engine=keda",
        api_versions=(KEDA_API,),
    )
    assert "t-weave-ingest-worker" not in _kinds(docs, "HorizontalPodAutoscaler")
    scaled = next(doc for doc in docs if doc["kind"] == "ScaledObject")
    triggers = {trigger["type"]: trigger for trigger in scaled["spec"]["triggers"]}
    assert triggers["redis"]["metadata"]["listName"] == "weave.ingest.ocr"
    assert "RUNNING" in triggers["postgresql"]["metadata"]["query"]
    auths = [doc for doc in docs if doc["kind"] == "TriggerAuthentication"]
    assert {ref["key"] for doc in auths for ref in doc["spec"]["secretTargetRef"]} == {
        "REDIS_PASSWORD", "POSTGRES_PASSWORD",
    }


def test_max_connections_is_set_on_bundled_and_cnpg_postgres() -> None:
    bundled = next(
        doc for doc in _docs() if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "t-weave-postgres"
    )
    assert bundled["spec"]["template"]["spec"]["containers"][0]["args"] == ["-c", "max_connections=200"]
    cnpg = next(
        doc for doc in _docs("postgresql.mode=cnpg", "dbBootstrap.enabled=false") if doc["kind"] == "Cluster"
    )
    assert cnpg["spec"]["postgresql"]["parameters"]["max_connections"] == "200"
