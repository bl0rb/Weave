"""Tests for scripts/weave_config.py.

Run with any Python that has pytest + PyYAML, e.g.:
    services/tools/.venv/bin/python -m pytest scripts/tests -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

import weave_config as wc

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# A small, self-contained weave.yaml used by most tests below, independent
# of the real repo weave.yaml so these tests stay stable as that file grows.
MINIMAL_YAML = """
shared:
  SHARED_SECRET:
    secret: true
    env_var: TEST_SHARED_SECRET
    required: true
    targets:
      - {service: alpha, var: SHARED_SECRET}
      - {service: beta, var: SHARED_SECRET}

  # Same underlying value, two DIFFERENT variable names across services --
  # the cross-name case (EMBEDDING_API_KEY / EMBEDDINGS_API_TOKEN in the
  # real config).
  CROSS_NAMED_SECRET:
    secret: true
    env_var: TEST_CROSS_NAMED_SECRET
    required: false
    targets:
      - {service: alpha, var: ALPHA_TOKEN}
      - {service: gamma, var: GAMMA_TOKEN}

  SHARED_MODEL:
    secret: false
    value: fake-embed
    type: string
    required: false
    targets:
      - {service: alpha, var: EMBEDDING_MODEL}
      - {service: beta, var: EMBEDDING_MODEL}

  SHARED_DIMENSION:
    secret: false
    value: 1536
    type: int
    required: false
    targets:
      - {service: alpha, var: EMBEDDING_DIMENSION}
      - {service: beta, var: EMBEDDING_DIMENSION}

services:
  alpha:
    settings:
      ALPHA_ONLY_SECRET:
        secret: true
        env_var: TEST_ALPHA_ONLY_SECRET
        required: true
      ALPHA_PORT:
        secret: false
        value: 9001
        type: int
        required: false

  beta:
    settings:
      BETA_LIST:
        secret: false
        value: ["a", "b"]
        type: json_list
        required: false

  gamma:
    settings: {}

  retrieval:
    settings:
      RERANK_PROVIDER:
        secret: false
        value: none
        type: string
        required: false
      SEARCH_TOP_K:
        secret: false
        value: 20
        type: int
        required: false
      EMBEDDING_MODEL:
        secret: false
        value: fake-embed
        type: string
        required: false
      EMBEDDING_DIMENSION:
        secret: false
        value: 1536
        type: int
        required: false

  reranker:
    settings:
      RERANKER_MAX_DOCUMENTS:
        secret: false
        value: 50
        type: int
        required: false

  knowledge:
    settings:
      EMBEDDING_MODEL:
        secret: false
        value: fake-embed
        type: string
        required: false
      EMBEDDING_DIMENSION:
        secret: false
        value: 1536
        type: int
        required: false
"""

REQUIRED_SECRET_ENV = {
    "TEST_SHARED_SECRET": "shared-secret-value",
    "TEST_ALPHA_ONLY_SECRET": "alpha-only-value",
}


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "weave.yaml"
    path.write_text(MINIMAL_YAML, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def test_render_writes_shared_value_once_and_mirrors_it(tmp_path, config_path):
    out = tmp_path / "out.env"
    rc = wc.render(config_path, out, environ=dict(REQUIRED_SECRET_ENV))
    assert rc == 0

    rendered = wc.parse_env_file(out)
    # Same variable name on both sides -> exactly one line in the file,
    # correctly holding the shared value.
    assert rendered["SHARED_SECRET"] == "shared-secret-value"
    assert list(out.read_text().splitlines()).count(
        "SHARED_SECRET=shared-secret-value"
    ) == 1


def test_render_mirrors_cross_named_shared_secret_into_both_variables(tmp_path, config_path):
    out = tmp_path / "out.env"
    environ = dict(REQUIRED_SECRET_ENV, TEST_CROSS_NAMED_SECRET="cross-value")
    rc = wc.render(config_path, out, environ=environ)
    assert rc == 0

    rendered = wc.parse_env_file(out)
    assert rendered["ALPHA_TOKEN"] == "cross-value"
    assert rendered["GAMMA_TOKEN"] == "cross-value"


def test_render_mirrors_shared_non_secret_defaults_identically(tmp_path, config_path):
    out = tmp_path / "out.env"
    rc = wc.render(config_path, out, environ=dict(REQUIRED_SECRET_ENV))
    assert rc == 0

    rendered = wc.parse_env_file(out)
    assert rendered["EMBEDDING_MODEL"] == "fake-embed"
    assert rendered["EMBEDDING_DIMENSION"] == "1536"


def test_render_serializes_json_list(tmp_path, config_path):
    out = tmp_path / "out.env"
    rc = wc.render(config_path, out, environ=dict(REQUIRED_SECRET_ENV))
    assert rc == 0
    rendered = wc.parse_env_file(out)
    assert rendered["BETA_LIST"] == '["a","b"]'


def test_render_fails_and_lists_every_missing_required_secret(tmp_path, config_path):
    out = tmp_path / "out.env"
    rc = wc.render(config_path, out, environ={})
    assert rc == 1
    assert not out.exists()


def test_render_allows_env_override_of_non_secret_default(tmp_path, config_path):
    out = tmp_path / "out.env"
    environ = dict(REQUIRED_SECRET_ENV, ALPHA_PORT="9999")
    rc = wc.render(config_path, out, environ=environ)
    assert rc == 0
    rendered = wc.parse_env_file(out)
    assert rendered["ALPHA_PORT"] == "9999"


# ---------------------------------------------------------------------------
# check: shared drift
# ---------------------------------------------------------------------------

def test_check_flags_drifted_shared_secret(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text("SHARED_SECRET=one-value\n", encoding="utf-8")
    other_file = tmp_path / "beta.env"
    other_file.write_text("SHARED_SECRET=different-value\n", encoding="utf-8")

    findings = wc.check(config_path, env_file, {"beta": other_file})
    messages = [f.message for f in findings]
    assert any("SHARED_SECRET" in m and "auseinander" in m for m in messages)


def test_check_flags_drifted_cross_named_secret(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "ALPHA_TOKEN=value-a\nGAMMA_TOKEN=value-b\n", encoding="utf-8"
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert any("CROSS_NAMED_SECRET" in m for m in messages)


def test_check_passes_when_shared_values_match(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=same-value\nALPHA_ONLY_SECRET=x\n", encoding="utf-8"
    )
    findings = wc.check(config_path, env_file)
    drift_messages = [f.message for f in findings if "auseinander" in f.message]
    assert drift_messages == []


# ---------------------------------------------------------------------------
# check: missing required values
# ---------------------------------------------------------------------------

def test_check_flags_missing_required_value(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text("SHARED_SECRET=x\n", encoding="utf-8")  # ALPHA_ONLY_SECRET missing

    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert any("ALPHA_ONLY_SECRET" in m and "Pflichtwert fehlt" in m for m in messages)


def test_check_passes_when_all_required_values_present(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n", encoding="utf-8"
    )
    findings = wc.check(config_path, env_file)
    required_messages = [f.message for f in findings if "Pflichtwert fehlt" in f.message]
    assert required_messages == []


# ---------------------------------------------------------------------------
# check: SEARCH_TOP_K vs RERANKER_MAX_DOCUMENTS
# ---------------------------------------------------------------------------

def test_check_flags_top_k_exceeding_max_documents_when_rerank_is_api(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n"
        "RERANK_PROVIDER=api\nSEARCH_TOP_K=100\nRERANKER_MAX_DOCUMENTS=50\n",
        encoding="utf-8",
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert any("SEARCH_TOP_K" in m and "RERANKER_MAX_DOCUMENTS" in m for m in messages)


def test_check_ignores_top_k_when_rerank_provider_is_not_api(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n"
        "RERANK_PROVIDER=none\nSEARCH_TOP_K=100\nRERANKER_MAX_DOCUMENTS=50\n",
        encoding="utf-8",
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert not any("RERANKER_MAX_DOCUMENTS" in m for m in messages)


def test_check_passes_when_top_k_within_max_documents(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n"
        "RERANK_PROVIDER=api\nSEARCH_TOP_K=20\nRERANKER_MAX_DOCUMENTS=50\n",
        encoding="utf-8",
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert not any("RERANKER_MAX_DOCUMENTS" in m for m in messages)


# ---------------------------------------------------------------------------
# check: EMBEDDING_DIMENSION vs known model
# ---------------------------------------------------------------------------

def test_check_flags_dimension_mismatch_for_known_model(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n"
        "EMBEDDING_MODEL=intfloat/multilingual-e5-small\nEMBEDDING_DIMENSION=1536\n",
        encoding="utf-8",
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert any(
        "EMBEDDING_DIMENSION" in m and "multilingual-e5-small" in m for m in messages
    )


def test_check_ignores_dimension_for_unknown_model(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n"
        "EMBEDDING_MODEL=fake-embed\nEMBEDDING_DIMENSION=42\n",
        encoding="utf-8",
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert not any("EMBEDDING_DIMENSION" in m for m in messages)


def test_check_passes_dimension_matching_known_model(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n"
        "EMBEDDING_MODEL=intfloat/multilingual-e5-small\nEMBEDDING_DIMENSION=384\n",
        encoding="utf-8",
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert not any("EMBEDDING_DIMENSION" in m for m in messages)


# ---------------------------------------------------------------------------
# check: CORS_ORIGINS vs FRONTEND_PORT
# ---------------------------------------------------------------------------

def _cors_env(tmp_path: Path, body: str) -> Path:
    env_file = tmp_path / "deploy.env"
    env_file.write_text(
        "SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n" + body, encoding="utf-8"
    )
    return env_file


def test_check_flags_cors_origins_missing_the_published_frontend_port(
    tmp_path, config_path
):
    env_file = _cors_env(
        tmp_path,
        'FRONTEND_PORT=3002\nCORS_ORIGINS=["http://localhost:3000"]\n',
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert any("CORS_ORIGINS" in m and "FRONTEND_PORT" in m for m in messages)


def test_check_passes_when_cors_origins_names_the_published_port(
    tmp_path, config_path
):
    env_file = _cors_env(
        tmp_path,
        'FRONTEND_PORT=3002\nCORS_ORIGINS=["http://localhost:3002"]\n',
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert not any("CORS_ORIGINS" in m for m in messages)


def test_check_ignores_cors_origins_behind_a_reverse_proxy(tmp_path, config_path):
    # A real hostname means the browser never sees FRONTEND_PORT -- the ports
    # legitimately differ, so the check must stay quiet.
    env_file = _cors_env(
        tmp_path,
        'FRONTEND_PORT=3000\nCORS_ORIGINS=["https://app.example.com"]\n',
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert not any("CORS_ORIGINS" in m for m in messages)


def test_check_flags_cors_origins_that_is_not_json(tmp_path, config_path):
    env_file = _cors_env(
        tmp_path, "FRONTEND_PORT=3000\nCORS_ORIGINS=http://localhost:3000\n"
    )
    findings = wc.check(config_path, env_file)
    messages = [f.message for f in findings]
    assert any("CORS_ORIGINS" in m and "JSON" in m for m in messages)


# ---------------------------------------------------------------------------
# check: never leaks a secret's actual value
# ---------------------------------------------------------------------------

def test_check_never_prints_a_secret_value(tmp_path, config_path):
    env_file = tmp_path / "deploy.env"
    env_file.write_text("SHARED_SECRET=super-secret-value-one\n", encoding="utf-8")
    other_file = tmp_path / "beta.env"
    other_file.write_text("SHARED_SECRET=super-secret-value-two\n", encoding="utf-8")

    findings = wc.check(config_path, env_file, {"beta": other_file})
    all_text = "\n".join(f.message for f in findings)

    assert "super-secret-value-one" not in all_text
    assert "super-secret-value-two" not in all_text
    # Fingerprints, not raw values, are what should show up instead.
    assert "sha256:" in all_text


def test_check_never_prints_secret_value_via_cli(tmp_path, config_path, capsys):
    env_file = tmp_path / "deploy.env"
    env_file.write_text("SHARED_SECRET=terminal-leak-test-value\n", encoding="utf-8")
    other_file = tmp_path / "beta.env"
    other_file.write_text("SHARED_SECRET=another-secret-value\n", encoding="utf-8")

    rc = wc.main([
        "--config", str(config_path),
        "check",
        "--env-file", str(env_file),
        "--service-env", f"beta={other_file}",
    ])
    out = capsys.readouterr().out
    assert rc == 1
    assert "terminal-leak-test-value" not in out
    assert "another-secret-value" not in out


# ---------------------------------------------------------------------------
# sanity checks against the REAL repo weave.yaml
# ---------------------------------------------------------------------------

def test_real_weave_yaml_parses():
    items = wc.load_items(REPO_ROOT / "weave.yaml")
    assert len(items) > 80


def test_real_weave_yaml_required_secrets_match_betrieb_md_section_4():
    items = wc.load_items(REPO_ROOT / "weave.yaml")
    required_env_vars = {
        item.env_var for item in items if item.secret and item.required
    }
    # docs/betrieb.md section 4 lists 14 Pflichtwert rows that collapse to
    # 9 distinct required secrets here (rows sharing one secret across
    # services -- e.g. WEAVE_DELEGATION_SECRET is required on both runtime
    # and tools -- collapse to one env var to export; the tenth row,
    # CORS_ORIGINS, is not a secret and is checked separately below; the
    # WEAVE_INGEST_BASE_URL row is out of this file's scope, see weave.yaml's
    # own header comment) plus two infrastructure secrets this file adds on
    # top (POSTGRES_PASSWORD, RETRIEVAL_DB_PASSWORD) that docs/betrieb.md
    # does not list under "Pflichtwerte" but that are just as required for
    # the stack to come up at all.
    assert required_env_vars == {
        "WEAVE_INGEST_SECRET_KEY",
        "WEAVE_REDIS_PASSWORD",
        "WEAVE_POSTGRES_PASSWORD",
        "WEAVE_KNOWLEDGE_INGEST_API_TOKEN",
        "WEAVE_KNOWLEDGE_WEBHOOK_SECRET",
        "RETRIEVAL_API_TOKEN",
        "RUNTIME_API_TOKEN",
        "WEAVE_DELEGATION_SECRET",
        "INTROSPECTION_SERVICE_TOKEN",
        "TOOLS_API_TOKEN",
        "WEAVE_RETRIEVAL_DB_PASSWORD",
    }


def test_real_weave_yaml_cors_origins_is_required_non_secret():
    items = wc.load_items(REPO_ROOT / "weave.yaml")
    cors = next(i for i in items if i.key == "CORS_ORIGINS")
    assert cors.required is True
    assert cors.secret is False


def test_real_weave_yaml_renders_and_then_checks_clean(tmp_path):
    out = tmp_path / "deploy.env"
    items = wc.load_items(REPO_ROOT / "weave.yaml")
    environ = {
        item.env_var: f"test-value-for-{item.env_var}"
        for item in items
        if item.secret and item.required
    }
    rc = wc.render(REPO_ROOT / "weave.yaml", out, environ=environ)
    assert rc == 0

    findings = wc.check(REPO_ROOT / "weave.yaml", out)
    assert findings == []
