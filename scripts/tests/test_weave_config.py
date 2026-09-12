"""Tests for scripts/weave_config.py.

Run with any Python that has pytest + PyYAML, e.g.:
    services/tools/.venv/bin/python -m pytest scripts/tests -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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


def test_render_creates_secret_file_with_restrictive_permissions(tmp_path, config_path):
    out = tmp_path / "out.env"
    rc = wc.render(config_path, out, environ=dict(REQUIRED_SECRET_ENV))
    assert rc == 0
    assert out.stat().st_mode & 0o777 == 0o600


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
# check: the federated-login chain
# ---------------------------------------------------------------------------

COMPLETE_LOGIN_CHAIN = (
    "HANDOFF_CALLBACK_URL=http://localhost:8004/v1/auth/ingest/callback\n"
    "INGEST_LOGIN_URL=http://localhost:3002/login\n"
    "INGEST_API_URL=http://weave-ingest-backend:8000\n"
    "HANDOFF_SECRET=s3cret\n"
    "WEAVE_API_INGEST_LOGIN_ENABLED=true\n"
    "CHAT_APP_BASE_URL=http://localhost:3001\n"
    'OIDC_POST_LOGIN_ALLOWED_URLS=["http://localhost:3001/api/auth/sso/callback"]\n'
)


def _chain_env(tmp_path: Path, body: str) -> Path:
    env_file = tmp_path / "deploy.env"
    env_file.write_text("SHARED_SECRET=x\nALPHA_ONLY_SECRET=y\n" + body, encoding="utf-8")
    return env_file


def test_check_passes_on_a_complete_login_chain(tmp_path, config_path):
    findings = wc.check(config_path, _chain_env(tmp_path, COMPLETE_LOGIN_CHAIN))

    assert [f.message for f in findings] == []


def test_check_says_nothing_when_the_login_chain_is_switched_off(tmp_path, config_path):
    findings = wc.check(config_path, _chain_env(tmp_path, ""))

    assert [f.message for f in findings] == []


def test_check_flags_a_missing_handoff_secret(tmp_path, config_path):
    body = COMPLETE_LOGIN_CHAIN.replace("HANDOFF_SECRET=s3cret\n", "")

    messages = [f.message for f in wc.check(config_path, _chain_env(tmp_path, body))]

    assert any("WEAVE_HANDOFF_SECRET" in m and "503" in m for m in messages)


def test_check_flags_a_callback_url_without_a_login_url(tmp_path, config_path):
    body = COMPLETE_LOGIN_CHAIN.replace("INGEST_LOGIN_URL=http://localhost:3002/login\n", "")

    messages = [f.message for f in wc.check(config_path, _chain_env(tmp_path, body))]

    assert any("INGEST_LOGIN_URL" in m for m in messages)


def test_check_flags_a_hidden_button(tmp_path, config_path):
    body = COMPLETE_LOGIN_CHAIN.replace("WEAVE_API_INGEST_LOGIN_ENABLED=true", "WEAVE_API_INGEST_LOGIN_ENABLED=")

    messages = [f.message for f in wc.check(config_path, _chain_env(tmp_path, body))]

    assert any("WEAVE_API_INGEST_LOGIN_ENABLED" in m for m in messages)


def test_check_flags_a_chat_callback_missing_from_the_allowlist(tmp_path, config_path):
    # The nastiest half-configuration: the login WORKS and still leaves the
    # user on the gateway, because an unlisted return_to is ignored on
    # purpose rather than reported.
    body = COMPLETE_LOGIN_CHAIN.replace(
        'OIDC_POST_LOGIN_ALLOWED_URLS=["http://localhost:3001/api/auth/sso/callback"]',
        'OIDC_POST_LOGIN_ALLOWED_URLS=["http://elsewhere.example/cb"]',
    )

    messages = [f.message for f in wc.check(config_path, _chain_env(tmp_path, body))]

    assert any("OIDC_POST_LOGIN_ALLOWED_URLS" in m and "sso/callback" in m for m in messages)


# ---------------------------------------------------------------------------
# check: the .env drifting away from weave.yaml
# ---------------------------------------------------------------------------

def _rendered_env(tmp_path: Path, config_path: Path) -> Path:
    out = tmp_path / "rendered.env"
    assert wc.render(config_path, out, environ=REQUIRED_SECRET_ENV) == 0
    return out


def test_check_passes_on_a_freshly_rendered_env(tmp_path, config_path):
    findings = wc.check(config_path, _rendered_env(tmp_path, config_path))

    assert [f.message for f in findings] == []


def test_check_flags_a_value_added_to_the_env_by_hand(tmp_path, config_path):
    env_file = _rendered_env(tmp_path, config_path)
    env_file.write_text(env_file.read_text() + "SOMETHING_NEW=1\n", encoding="utf-8")

    messages = [f.message for f in wc.check(config_path, env_file)]

    assert any("Von Hand" in m and "SOMETHING_NEW" in m for m in messages)


def test_check_flags_an_env_older_than_weave_yaml(tmp_path, config_path):
    env_file = _rendered_env(tmp_path, config_path)
    # weave.yaml grew a setting after this .env was rendered
    config_path.write_text(
        config_path.read_text() + """
  delta:
    settings:
      DELTA_LATER:
        secret: false
        value: 1
        type: int
        required: false
""",
        encoding="utf-8",
    )

    messages = [f.message for f in wc.check(config_path, env_file)]

    assert any("aelter als" in m and "DELTA_LATER" in m for m in messages)


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
    # docs/betrieb.md section 4 lists the Pflichtwert rows; those sharing one
    # secret across services (WEAVE_DELEGATION_SECRET on runtime AND tools,
    # CHAT_CONFIG_SERVICE_TOKEN on ingest AND runtime) collapse to one env
    # var to export here. CORS_ORIGINS is not a secret and is checked
    # separately below; the WEAVE_INGEST_BASE_URL row is out of this file's
    # scope, see weave.yaml's own header comment. Two infrastructure secrets
    # (POSTGRES_PASSWORD, RETRIEVAL_DB_PASSWORD) come on top: betrieb.md does
    # not list them under "Pflichtwerte", but the stack cannot come up
    # without them.
    #
    # A new entry here means a new row in betrieb.md section 4 (and the
    # handbook) -- that is what this test is for.
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
        # ADR-0007: Ingest holds the chat-provider configuration, Runtime
        # reads it per turn through this shared bearer.
        "CHAT_CONFIG_SERVICE_TOKEN",
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


# ---------------------------------------------------------------------------
# helm-values
# ---------------------------------------------------------------------------

def _helm_doc(tmp_path: Path, config_path: Path) -> dict:
    """Run helm-values against a config and parse the file back as YAML."""
    out = tmp_path / "values.generated.yaml"
    rc = wc.helm_values(config_path, out)
    assert rc == 0
    return yaml.safe_load(out.read_text(encoding="utf-8"))


def test_helm_values_groups_non_secrets_by_service(tmp_path, config_path):
    doc = _helm_doc(tmp_path, config_path)
    assert doc["config"]["alpha"]["ALPHA_PORT"] == "9001"
    assert doc["config"]["alpha"]["EMBEDDING_MODEL"] == "fake-embed"
    assert doc["config"]["beta"]["EMBEDDING_MODEL"] == "fake-embed"


def test_helm_values_lists_only_secret_names(tmp_path, config_path):
    doc = _helm_doc(tmp_path, config_path)
    assert doc["secrets"]["alpha"] == ["ALPHA_ONLY_SECRET", "ALPHA_TOKEN", "SHARED_SECRET"]
    assert doc["secrets"]["beta"] == ["SHARED_SECRET"]
    assert doc["secrets"]["gamma"] == ["GAMMA_TOKEN"]


def test_helm_values_never_writes_a_secret_value_or_placeholder(
    tmp_path, config_path, monkeypatch
):
    # Every required secret exported, exactly as a `render` would need them --
    # helm-values must ignore them all the same.
    environ = dict(REQUIRED_SECRET_ENV, TEST_CROSS_NAMED_SECRET="cross-value")
    for name, value in environ.items():
        monkeypatch.setenv(name, value)

    out = tmp_path / "values.generated.yaml"
    assert wc.helm_values(config_path, out) == 0
    text = out.read_text(encoding="utf-8")
    for name, value in environ.items():
        assert value not in text, f"{name} leaked into {out.name}"

    doc = yaml.safe_load(text)
    # A secret appears as a bare name in a list, never as a key with a value
    # -- not even an empty string or a "changeme" placeholder.
    for names in doc["secrets"].values():
        assert isinstance(names, list)
        for entry in names:
            assert isinstance(entry, str)
    # ... and nowhere in the config half either.
    for settings in doc["config"].values():
        assert "SHARED_SECRET" not in settings
        assert "ALPHA_ONLY_SECRET" not in settings


def test_helm_values_ignores_environment_overrides(tmp_path, config_path, monkeypatch):
    # `render` honours an exported ALPHA_PORT; a generated chart values file
    # must describe the platform, not the machine that generated it.
    monkeypatch.setenv("ALPHA_PORT", "9999")
    doc = _helm_doc(tmp_path, config_path)
    assert doc["config"]["alpha"]["ALPHA_PORT"] == "9001"


def test_helm_values_serializes_like_the_env_render(tmp_path, config_path):
    env_out = tmp_path / "out.env"
    assert wc.render(config_path, env_out, environ=dict(REQUIRED_SECRET_ENV)) == 0
    rendered = wc.parse_env_file(env_out)

    doc = _helm_doc(tmp_path, config_path)
    # json_list stays compact JSON, ints stay unpadded digits -- byte-for-byte
    # what the .env carries, so a container sees the same string either way.
    assert doc["config"]["beta"]["BETA_LIST"] == rendered["BETA_LIST"] == '["a","b"]'
    assert doc["config"]["alpha"]["EMBEDDING_DIMENSION"] == rendered["EMBEDDING_DIMENSION"]


def test_helm_values_gives_a_secret_only_service_an_empty_config_block(tmp_path, config_path):
    # gamma has no settings of its own, only a shared secret target. The block
    # still exists so a template can index config.<service> without a nil check.
    doc = _helm_doc(tmp_path, config_path)
    assert doc["config"]["gamma"] == {}


def test_helm_values_header_names_the_regenerating_command(tmp_path, config_path):
    out = tmp_path / "values.generated.yaml"
    assert wc.helm_values(config_path, out) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith(wc.HELM_VALUES_HEADER)
    assert "weave_config.py helm-values" in text
    assert "DO NOT EDIT BY HAND" in text


def test_helm_values_cli_writes_the_requested_file(tmp_path, config_path):
    out = tmp_path / "nested" / "values.generated.yaml"
    rc = wc.main(["--config", str(config_path), "helm-values", "--out", str(out)])
    assert rc == 0
    assert out.exists()


def test_real_weave_yaml_helm_values_covers_every_service(tmp_path):
    doc = _helm_doc(tmp_path, REPO_ROOT / "weave.yaml")
    items = wc.load_items(REPO_ROOT / "weave.yaml")
    services = {service for item in items for service, _var in item.targets}
    assert set(doc["config"]) == services
    assert set(doc["secrets"]) == services


def test_real_weave_yaml_helm_values_matches_the_env_render(tmp_path):
    """The chart and the compose stack must see identical non-secret values --
    that is the whole point of both reading weave.yaml."""
    items = wc.load_items(REPO_ROOT / "weave.yaml")
    env_out = tmp_path / "deploy.env"
    environ = {
        item.env_var: f"test-value-for-{item.env_var}"
        for item in items
        if item.secret and item.required
    }
    assert wc.render(REPO_ROOT / "weave.yaml", env_out, environ=environ) == 0
    rendered = wc.parse_env_file(env_out)

    doc = _helm_doc(tmp_path, REPO_ROOT / "weave.yaml")
    for service, settings in doc["config"].items():
        for var, value in settings.items():
            assert value == rendered[var], f"{service}.{var} drifted from the .env"


def test_real_weave_yaml_helm_values_leaks_no_required_secret(tmp_path):
    out = tmp_path / "values.generated.yaml"
    assert wc.helm_values(REPO_ROOT / "weave.yaml", out) == 0
    text = out.read_text(encoding="utf-8")
    items = wc.load_items(REPO_ROOT / "weave.yaml")
    secret_vars = {var for item in items if item.secret for _s, var in item.targets}
    doc = yaml.safe_load(text)
    for settings in doc["config"].values():
        assert not (set(settings) & secret_vars)
